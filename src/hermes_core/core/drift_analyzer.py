"""drift_analyzer.py — System drift detection and analysis.


import logging

logger = logging.getLogger(__name__)

Analyzes telemetry, memory, goals, events, and reflection data to detect
behavioral drift patterns: deepening planner trees, memory pollution, goal
instability, event storms, and reflective noise.

Dependencies (all optional, imported with try/except):
    telemetry.py, event_logger.py, memory_manager.py, planner.py,
    goal_manager.py, reflection_engine.py
"""
from __future__ import annotations
import threading
from datetime import datetime, timezone
from typing import Any, Optional
try:
    from . import telemetry
except (ImportError, ModuleNotFoundError):
    telemetry = None
try:
    from . import event_logger
except (ImportError, ModuleNotFoundError):
    event_logger = None
try:
    from . import memory_manager
except (ImportError, ModuleNotFoundError):
    memory_manager = None
try:
    from . import planner
except (ImportError, ModuleNotFoundError):
    planner = None
try:
    from . import goal_manager
except (ImportError, ModuleNotFoundError):
    goal_manager = None
try:
    from . import reflection_engine
except (ImportError, ModuleNotFoundError):
    reflection_engine = None
_DRIFT_WEIGHTS: dict[str, float] = {'planner': 0.25, 'memory': 0.2, 'goal': 0.2, 'event': 0.2, 'reflection': 0.15}
_STABLE_THRESHOLD = 0.3
_MILD_THRESHOLD = 0.2
_CONCERNING_THRESHOLD = 0.4
_CRITICAL_THRESHOLD = 0.6
_instance: Optional['DriftAnalyzer'] = None
_instance_lock = threading.Lock()

def get_drift_analyzer() -> 'DriftAnalyzer':
    """Return the module-level ``DriftAnalyzer`` singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = DriftAnalyzer()
    return _instance

def _safe_float(val: Any, default: float=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def _clamp(val: float, lo: float=0.0, hi: float=1.0) -> float:
    return max(lo, min(hi, val))

def _trend(values: list[float]) -> str:
    """Determine the trend of a numeric sequence."""
    if len(values) < 3:
        return 'stable'
    n = len(values)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum(((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n)))
    den = sum(((xs[i] - mean_x) ** 2 for i in range(n)))
    if den == 0:
        return 'stable'
    slope = num / den
    if slope > 0.01 * mean_y if mean_y > 0 else slope > 0.001:
        return 'increasing'
    if slope < -0.01 * mean_y if mean_y > 0 else slope < -0.001:
        return 'decreasing'
    return 'stable'

def _growth_trend(values: list[float]) -> str:
    """Check if a sequence grows linearly or exponentially."""
    if len(values) < 4:
        return 'linear'
    mid = len(values) // 2
    first_half = values[:mid]
    last_half = values[mid:]
    if not first_half or not last_half:
        return 'linear'
    first_avg = sum(first_half) / len(first_half)
    last_avg = sum(last_half) / len(last_half)
    if first_avg <= 0:
        return 'linear'
    ratio = last_avg / first_avg
    if ratio > 2.5:
        return 'exponential'
    if ratio > 1.3:
        return 'accelerating'
    return 'linear'

class DriftAnalyzer:
    """Singleton that analyzes multiple signal sources for system drift.

    Usage
    -----
    >>> da = DriftAnalyzer()
    >>> result = da.analyze_all(telemetry_history=[...], ...)
    >>> summary = da.get_summary()
    """
    _instance: Optional['DriftAnalyzer'] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> 'DriftAnalyzer':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    obj._initialized = False
                    cls._instance = obj
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, '_initialized', False):
            return
        self._last_results: dict[str, Any] = {}
        self._initialized = True


    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None

    def analyze_planner_drift(self, telemetry_history: list[dict]) -> dict:
        """Track planner depth over time and detect divergence.

        Returns
        -------
        dict
            ``current_depth``, ``avg_depth``, ``depth_trend``,
            ``drift_score`` (0-1).
        """
        depths = [int(e.get('planner_depth', 0)) for e in telemetry_history if e]
        if not depths:
            return {'current_depth': 0, 'avg_depth': 0.0, 'depth_trend': 'stable', 'drift_score': 0.0}
        current_depth = depths[-1]
        avg_depth = sum(depths) / len(depths)
        depth_trend = _trend(depths)
        max_possible = max(depths + [5])
        depth_ratio = current_depth / max_possible if max_possible > 0 else 0.0
        trend_penalty = 0.0
        if depth_trend == 'increasing':
            trend_penalty = 0.3
        elif depth_trend == 'decreasing':
            trend_penalty = -0.1
        drift_score = _clamp(depth_ratio * 0.7 + trend_penalty)
        return {'current_depth': current_depth, 'avg_depth': round(avg_depth, 2), 'depth_trend': depth_trend, 'drift_score': round(drift_score, 4)}

    def analyze_memory_drift(self, memory_snapshots: list[dict]) -> dict:
        """Track memory growth and detect pollution.

        Parameters
        ----------
        memory_snapshots : list[dict]
            Each dict should have ``total_entries`` (int) and optionally
            ``unique_entries`` (int).

        Returns
        -------
        dict
            ``growth_rate``, ``memory_entropy`` (0-1),
            ``pollution_risk`` ('low'|'medium'|'high'),
            ``old_to_new_ratio``.
        """
        if not memory_snapshots:
            return {'growth_rate': 'stable', 'memory_entropy': 0.0, 'pollution_risk': 'low', 'old_to_new_ratio': 0.0}
        counts = [int(s.get('total_entries', 0)) for s in memory_snapshots]
        unique = [int(s.get('unique_entries', c)) for c, s in zip(counts, memory_snapshots)]
        growth_rate = _growth_trend(counts)
        total = counts[-1] if counts else 1
        unique_val = unique[-1] if unique else total
        entropy = _clamp(unique_val / total if total > 0 else 1.0)
        if entropy < 0.3:
            pollution_risk = 'high'
        elif entropy < 0.6:
            pollution_risk = 'medium'
        else:
            pollution_risk = 'low'
        if len(counts) >= 2:
            old_count = counts[0]
            new_count = counts[-1] - old_count
            old_to_new = old_count / new_count if new_count > 0 else float('inf')
        else:
            old_to_new = 0.0
        return {'growth_rate': growth_rate, 'memory_entropy': round(entropy, 4), 'pollution_risk': pollution_risk, 'old_to_new_ratio': round(old_to_new, 4) if old_to_new != float('inf') else 99.0}

    def analyze_goal_drift(self, goal_history: list[dict]) -> dict:
        """Track goal switching and cancellation instability.

        Parameters
        ----------
        goal_history : list[dict]
            Each dict should have ``status``, ``created_at``, and optionally
            ``started_at`` and ``completed_at``.

        Returns
        -------
        dict
            ``switch_frequency``, ``cancel_rate``, ``avg_goal_duration``,
            ``instability_score`` (0-1).
        """
        if not goal_history:
            return {'switch_frequency': 0.0, 'cancel_rate': 0.0, 'avg_goal_duration': 0.0, 'instability_score': 0.0}
        total = len(goal_history)
        cancelled = sum((1 for g in goal_history if g.get('status') == 'cancelled'))
        completed = sum((1 for g in goal_history if g.get('status') == 'completed'))
        failed = sum((1 for g in goal_history if g.get('status') in ('failed', 'error')))
        cancel_rate = cancelled / total if total > 0 else 0.0
        statuses = [g.get('status', 'unknown') for g in goal_history]
        switches = sum((1 for i in range(1, len(statuses)) if statuses[i] != statuses[i - 1]))
        switch_frequency = switches / len(statuses) if len(statuses) > 1 else 0.0
        durations: list[float] = []
        for g in goal_history:
            started = g.get('started_at') or g.get('created_at')
            completed_at = g.get('completed_at')
            if started and completed_at:
                try:
                    start_dt = datetime.fromisoformat(started)
                    end_dt = datetime.fromisoformat(completed_at)
                    durations.append((end_dt - start_dt).total_seconds())
                except (ValueError, TypeError):
                    pass
        avg_duration = sum(durations) / len(durations) if durations else 0.0
        fail_rate = failed / total if total > 0 else 0.0
        instability_score = _clamp(switch_frequency * 0.4 + cancel_rate * 0.35 + fail_rate * 0.25)
        return {'switch_frequency': round(switch_frequency, 4), 'cancel_rate': round(cancel_rate, 4), 'avg_goal_duration': round(avg_duration, 2), 'instability_score': round(instability_score, 4)}

    def analyze_event_drift(self, event_snapshots: list[dict]) -> dict:
        """Track event throughput, error ratios, and fragmentation.

        Parameters
        ----------
        event_snapshots : list[dict]
            Each dict should have ``total_events`` (int), ``by_severity``
            (dict), ``by_type`` (dict), and optionally ``file_size_bytes``.

        Returns
        -------
        dict
            ``current_throughput``, ``growth_trend``, ``error_ratio``,
            ``event_storm_risk`` (0-1), ``diversity_score``.
        """
        if not event_snapshots:
            return {'current_throughput': 0, 'growth_trend': 'stable', 'error_ratio': 0.0, 'event_storm_risk': 0.0, 'diversity_score': 0.0}
        totals = [int(s.get('total_events', 0)) for s in event_snapshots]
        growth_trend = _growth_trend(totals)
        current_throughput = totals[-1] if totals else 0
        error_events = 0
        total_events = 0
        for s in event_snapshots:
            by_sev = s.get('by_severity', {})
            if isinstance(by_sev, dict):
                error_events += by_sev.get('error', 0) + by_sev.get('critical', 0)
                total_events += sum(by_sev.values())
        error_ratio = error_events / total_events if total_events > 0 else 0.0
        all_types: set[str] = set()
        type_counts: dict[str, int] = {}
        for s in event_snapshots:
            by_type = s.get('by_type', {})
            if isinstance(by_type, dict):
                for t, c in by_type.items():
                    all_types.add(t)
                    type_counts[t] = type_counts.get(t, 0) + c
        total_type_instances = sum(type_counts.values())
        if total_type_instances > 0 and all_types:
            expected_per_type = total_type_instances / len(all_types)
            variance = sum(((c - expected_per_type) ** 2 for c in type_counts.values())) / len(all_types)
            max_variance = (total_type_instances - expected_per_type) ** 2
            diversity = 1.0 - variance / max_variance if max_variance > 0 else 0.0
            diversity = _clamp(diversity)
        else:
            diversity = 0.0
        growth_penalty = 0.0
        if growth_trend == 'exponential':
            growth_penalty = 0.4
        elif growth_trend == 'accelerating':
            growth_penalty = 0.2
        event_storm_risk = _clamp(growth_penalty * 0.5 + error_ratio * 0.3 + diversity * 0.2)
        return {'current_throughput': current_throughput, 'growth_trend': growth_trend, 'error_ratio': round(error_ratio, 4), 'event_storm_risk': round(event_storm_risk, 4), 'diversity_score': round(diversity, 4)}

    def analyze_reflection_drift(self, reflection_list: list[dict]) -> dict:
        """Track reflection volume, length trends, and self-referential noise.

        Parameters
        ----------
        reflection_list : list[dict]
            Each dict should have ``content`` or ``result_summary``,
            ``mistakes``, ``improvements``, and optionally ``created_at``.

        Returns
        -------
        dict
            ``reflection_count``, ``avg_length_trend``,
            ``self_referential_ratio``, ``noise_score`` (0-1).
        """
        if not reflection_list:
            return {'reflection_count': 0, 'avg_length_trend': 'stable', 'self_referential_ratio': 0.0, 'noise_score': 0.0}
        count = len(reflection_list)
        lengths: list[int] = []
        for r in reflection_list:
            content = r.get('content', '') or r.get('result_summary', '') or r.get('task_description', '')
            if isinstance(content, str):
                lengths.append(len(content))
            else:
                lengths.append(0)
        avg_length_trend = _trend(lengths) if len(lengths) >= 3 else 'stable'
        self_ref_count = 0
        keywords = ['previous reflection', 'last reflection', 'prior reflection', 'earlier reflection', 'reflection above', 'as noted before', 'as i reflected', 'previous thought']
        for r in reflection_list:
            text = ' '.join((str(v) for v in [r.get('content', ''), r.get('result_summary', ''), r.get('mistakes', ''), r.get('improvements', '')])).lower()
            if any((kw in text for kw in keywords)):
                self_ref_count += 1
        self_referential_ratio = self_ref_count / count if count > 0 else 0.0
        length_penalty = 0.0
        if avg_length_trend == 'increasing':
            length_penalty = 0.3
        seen_mistakes: set[str] = set()
        seen_improvements: set[str] = set()
        repeat_count = 0
        total_items = 0
        for r in reflection_list:
            for m in r.get('mistakes', []) or []:
                total_items += 1
                key = str(m).strip().lower()
                if key in seen_mistakes:
                    repeat_count += 1
                seen_mistakes.add(key)
            for imp in r.get('improvements', []) or []:
                total_items += 1
                key = str(imp).strip().lower()
                if key in seen_improvements:
                    repeat_count += 1
                seen_improvements.add(key)
        repetition_ratio = repeat_count / total_items if total_items > 0 else 0.0
        noise_score = _clamp(length_penalty * 0.3 + self_referential_ratio * 0.3 + repetition_ratio * 0.4)
        return {'reflection_count': count, 'avg_length_trend': avg_length_trend, 'self_referential_ratio': round(self_referential_ratio, 4), 'noise_score': round(noise_score, 4)}

    def analyze_all(self, telemetry_history: Optional[list[dict]]=None, memory_snapshots: Optional[list[dict]]=None, goal_history: Optional[list[dict]]=None, event_snapshots: Optional[list[dict]]=None, reflection_list: Optional[list[dict]]=None) -> dict:
        """Run all five drift analyses and compute an overall score.

        Parameters
        ----------
        telemetry_history : list of dict or None
            History of telemetry snapshots (planner_depth, etc.).
        memory_snapshots : list of dict or None
            Memory snapshots (total_entries, unique_entries).
        goal_history : list of dict or None
            Goal history (status, started_at, completed_at).
        event_snapshots : list of dict or None
            Event log snapshots (total_events, by_severity, by_type).
        reflection_list : list of dict or None
            Reflection entries (content, mistakes, improvements).

        Returns
        -------
        dict
            Results per subsystem, ``overall_drift_score``, ``stable``, ``alerts``.
        """
        planner_result = self.analyze_planner_drift(telemetry_history or [])
        memory_result = self.analyze_memory_drift(memory_snapshots or [])
        goal_result = self.analyze_goal_drift(goal_history or [])
        event_result = self.analyze_event_drift(event_snapshots or [])
        reflection_result = self.analyze_reflection_drift(reflection_list or [])
        overall = _DRIFT_WEIGHTS['planner'] * planner_result['drift_score'] + _DRIFT_WEIGHTS['memory'] * (1.0 - memory_result['memory_entropy']) + _DRIFT_WEIGHTS['goal'] * goal_result['instability_score'] + _DRIFT_WEIGHTS['event'] * event_result['event_storm_risk'] + _DRIFT_WEIGHTS['reflection'] * reflection_result['noise_score']
        overall = round(_clamp(overall), 4)
        stable = overall < _STABLE_THRESHOLD
        alerts: list[str] = []
        if planner_result['depth_trend'] == 'increasing':
            alerts.append(f"Planner depth trending upward (current: {planner_result['current_depth']}, avg: {planner_result['avg_depth']})")
        if memory_result['pollution_risk'] == 'high':
            alerts.append(f"Memory pollution risk HIGH (entropy: {memory_result['memory_entropy']})")
        if goal_result['instability_score'] > 0.5:
            alerts.append(f"Goal instability detected (score: {goal_result['instability_score']})")
        if event_result['event_storm_risk'] > 0.5:
            alerts.append(f"Event storm risk elevated (risk: {event_result['event_storm_risk']})")
        if reflection_result['noise_score'] > 0.5:
            alerts.append(f"Reflection noise high (score: {reflection_result['noise_score']})")
        if not stable:
            alerts.append(f'Overall drift score {overall} exceeds stability threshold ({_STABLE_THRESHOLD})')

        # Determine if action is required and suggest remediation
        action_required = not stable or len(alerts) > 0
        suggested_actions: list[str] = []
        if not stable:
            suggested_actions.append("raise_threshold: tighten default risk threshold to 'high'")
        if planner_result.get('drift_score', 0) > 0.5:
            suggested_actions.append("reset_planner: clear planner preferences and re-learn")
        if memory_result.get('pollution_risk') == 'high' or memory_result.get('memory_entropy', 0) > 0.6:
            suggested_actions.append("prune_memory: prune and deduplicate memory stores")
        if event_result.get('event_storm_risk', 0) > 0.6:
            suggested_actions.append("throttle_eventbus: reduce event publish rate")
        if goal_result.get('instability_score', 0) > 0.6:
            suggested_actions.append("reduce_concurrency: lower max_concurrent_tasks")
        if reflection_result.get('noise_score', 0) > 0.6:
            suggested_actions.append("reduce_reflection: suppress reflection engine frequency")

        result = {
            'planner': planner_result, 'memory': memory_result, 'goal': goal_result,
            'event': event_result, 'reflection': reflection_result,
            'overall_drift_score': overall, 'stable': stable, 'alerts': alerts,
            'action_required': action_required,
            'suggested_actions': suggested_actions,
        }
        self._last_results = result
        return result

    def get_summary(self) -> dict:
        """Return a human-readable summary of all drift metrics.

        Returns the last ``analyze_all()`` results, or an empty dict if
        ``analyze_all()`` has never been called.
        """
        if not self._last_results:
            return {'status': 'not yet analyzed', 'drift_level': 'unknown'}
        score = self._last_results['overall_drift_score']
        if score < _MILD_THRESHOLD:
            level = 'stable'
        elif score < _CONCERNING_THRESHOLD:
            level = 'mild drift'
        elif score < _CRITICAL_THRESHOLD:
            level = 'concerning'
        else:
            level = 'critical'
        return {'overall_drift_score': score, 'drift_level': level, 'stable': self._last_results['stable'], 'alert_count': len(self._last_results['alerts']), 'alerts': self._last_results['alerts'], 'subsystem_summary': {'planner': {'trend': self._last_results['planner']['depth_trend'], 'score': self._last_results['planner']['drift_score']}, 'memory': {'growth': self._last_results['memory']['growth_rate'], 'pollution_risk': self._last_results['memory']['pollution_risk']}, 'goal': {'instability': self._last_results['goal']['instability_score'], 'cancel_rate': self._last_results['goal']['cancel_rate']}, 'event': {'growth_trend': self._last_results['event']['growth_trend'], 'storm_risk': self._last_results['event']['event_storm_risk']}, 'reflection': {'count': self._last_results['reflection']['reflection_count'], 'noise': self._last_results['reflection']['noise_score']}}}

def collect_from_telemetry(tm: Any, n_points: int=100) -> dict:
    """Pull telemetry history from a ``Telemetry`` instance.

    Parameters
    ----------
    tm : Telemetry
        A ``Telemetry`` singleton instance (or any object with
        ``get_history(limit)``).
    n_points : int
        Maximum number of history points to fetch (default 100).

    Returns
    -------
    dict
        ``history`` (list of dict), ``latest`` (dict or None).
    """
    result: dict[str, Any] = {'history': [], 'latest': None}
    if tm is None:
        return result
    try:
        if hasattr(tm, 'get_history'):
            result['history'] = tm.get_history(limit=n_points)
        if hasattr(tm, 'get_latest'):
            latest = tm.get_latest()
            if hasattr(latest, '_asdict') or hasattr(latest, 'asdict'):
                try:
                    result['latest'] = latest.asdict()
                except Exception:
                    result['latest'] = latest
            elif hasattr(latest, '__dict__'):
                result['latest'] = latest.__dict__
            else:
                result['latest'] = latest
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    return result

def collect_from_event_bus(event_bus_instance: Any) -> dict:
    """Pull event statistics from an ``EventBus`` or ``EventLogger``.

    Parameters
    ----------
    event_bus_instance : EventBus or EventLogger
        Object with ``get_stats()`` method returning
        ``{total_events, by_severity, by_type, ...}``.

    Returns
    -------
    dict
        Event stats snapshot.
    """
    result: dict[str, Any] = {'total_events': 0, 'by_severity': {}, 'by_type': {}, 'file_size_bytes': 0}
    if event_bus_instance is None:
        return result
    try:
        if hasattr(event_bus_instance, 'get_stats'):
            stats = event_bus_instance.get_stats()
            if isinstance(stats, dict):
                result.update(stats)
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    return result

def collect_from_memory(mgr: Any) -> dict:
    """Pull a memory snapshot from a ``MemoryManager`` instance.

    Parameters
    ----------
    mgr : MemoryManager
        Object with methods for counting memory entries across layers.

    Returns
    -------
    dict
        ``total_entries``, ``unique_entries``, ``layers``.
    """
    result: dict[str, Any] = {'total_entries': 0, 'unique_entries': 0, 'layers': {}}
    if mgr is None:
        return result
    try:
        total = 0
        unique = 0
        layers: dict[str, dict] = {}
        for layer_name in ('episodic', 'semantic', 'procedural', 'working', 'environment'):
            layer_data: dict[str, Any] = {'count': 0}
            try:
                count_method = getattr(mgr, f'get_{layer_name}_count', None)
                if count_method:
                    layer_data['count'] = int(count_method())
                else:
                    layer_attr = getattr(mgr, f'_{layer_name}_memories', None) or getattr(mgr, f'{layer_name}_memories', None)
                    if isinstance(layer_attr, dict):
                        layer_data['count'] = len(layer_attr)
                    elif hasattr(layer_attr, '__len__'):
                        layer_data['count'] = len(layer_attr)
            except Exception as exc:
                logger.debug('drift_analyzer: get_summary: %s', exc)
            total += layer_data['count']
            layers[layer_name] = layer_data
        result['total_entries'] = total
        try:
            if hasattr(mgr, 'get_unique_entry_count'):
                unique = int(mgr.get_unique_entry_count())
            elif hasattr(mgr, 'get_health'):
                health = mgr.get_health()
                if isinstance(health, dict):
                    unique = health.get('unique_entries', total)
            else:
                unique = total
        except Exception:
            unique = total
        result['unique_entries'] = unique
        result['layers'] = layers
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    return result

def collect_from_planner(p: Any) -> dict:
    """Pull the current planner state.

    Parameters
    ----------
    p : Planner
        A ``Planner`` singleton instance.

    Returns
    -------
    dict
        ``plan_count``, ``max_depth``, ``active_plans``, ``budget``.
    """
    result: dict[str, Any] = {'plan_count': 0, 'max_depth': 0, 'active_plans': 0, 'budget': {}}
    if p is None:
        return result
    try:
        if hasattr(p, '_plans'):
            result['plan_count'] = len(p._plans)
        if hasattr(p, 'active_plans'):
            active = p.active_plans
            if isinstance(active, (int, float)):
                result['active_plans'] = int(active)
            elif hasattr(active, '__len__'):
                result['active_plans'] = len(active)
        try:
            from . import planner as _planner_mod
            if hasattr(_planner_mod, 'PLANNING_BUDGET'):
                result['budget'] = dict(_planner_mod.PLANNING_BUDGET)
                result['max_depth'] = _planner_mod.PLANNING_BUDGET.get('max_plan_depth', 5)
        except Exception as exc:
            logger.debug('drift_analyzer: get_summary: %s', exc)
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    return result

def analyze_drift() -> dict:
    """Convenience: collect data from all available modules and run drift analysis.

    Attempts to discover ``Telemetry``, ``EventLogger``, ``MemoryManager``,
    ``Planner``, ``GoalManager``, and ``ReflectionEngine`` singletons via
    their module-level ``get_*()`` accessors.

    Returns
    -------
    dict
        Full results from ``DriftAnalyzer.analyze_all()``.
    """
    da = get_drift_analyzer()
    telemetry_history: list[dict] = []
    memory_snapshots: list[dict] = []
    goal_history: list[dict] = []
    event_snapshots: list[dict] = []
    reflection_list: list[dict] = []
    try:
        if telemetry is not None and hasattr(telemetry, 'get_telemetry'):
            tm = telemetry.get_telemetry()
            collected = collect_from_telemetry(tm)
            telemetry_history = collected.get('history', [])
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    try:
        if event_logger is not None and hasattr(event_logger, 'get_logger'):
            el = event_logger.get_logger()
            snap = collect_from_event_bus(el)
            event_snapshots.append(snap)
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    try:
        if memory_manager is not None and hasattr(memory_manager, 'get_memory_manager'):
            mm = memory_manager.get_memory_manager()
            snap = collect_from_memory(mm)
            memory_snapshots.append(snap)
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    try:
        if planner is not None and hasattr(planner, 'get_planner'):
            p = planner.get_planner()
            state = collect_from_planner(p)
            if not telemetry_history and state.get('max_depth', 0) > 0:
                telemetry_history.append({'planner_depth': state['max_depth'], 'timestamp': datetime.now(timezone.utc).isoformat()})
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    try:
        if goal_manager is not None and hasattr(goal_manager, 'GoalManager'):
            gm = goal_manager.GoalManager()
            if hasattr(gm, 'get_all_goals'):
                goals = gm.get_all_goals()
                goal_history = [{'status': g.status if hasattr(g, 'status') else 'unknown', 'created_at': g.created_at if hasattr(g, 'created_at') else '', 'started_at': g.started_at if hasattr(g, 'started_at') else None, 'completed_at': g.completed_at if hasattr(g, 'completed_at') else None} for g in goals]
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    try:
        if reflection_engine is not None and hasattr(reflection_engine, 'ReflectionEngine'):
            re_instance = reflection_engine.ReflectionEngine()
            if hasattr(re_instance, 'get_recent_reflections'):
                reflections = re_instance.get_recent_reflections(limit=100)
                reflection_list = [{'content': r.result_summary if hasattr(r, 'result_summary') else '', 'result_summary': getattr(r, 'result_summary', ''), 'task_description': getattr(r, 'task_description', ''), 'mistakes': getattr(r, 'mistakes', []), 'improvements': getattr(r, 'improvements', []), 'created_at': getattr(r, 'created_at', '')} for r in reflections]
    except Exception as exc:
        logger.debug('drift_analyzer: get_summary: %s', exc)
    return da.analyze_all(telemetry_history=telemetry_history, memory_snapshots=memory_snapshots, goal_history=goal_history, event_snapshots=event_snapshots, reflection_list=reflection_list)