"""

import logging

logger = logging.getLogger(__name__)

telemetry.py — Cognitive Health Monitoring

Tracks system-level and cognitive metrics, computes a composite
cognitive_stability_score, and publishes alerts when stability drops.

Dependencies (all optional, imported with try/except):
    event_bus.py, event_logger.py, world_model.py, kernel.py, exceptions.py
"""
from __future__ import annotations
import atexit
import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional
try:
    from . import event_bus
except (ImportError, ModuleNotFoundError):
    event_bus = None
try:
    from . import event_logger
except (ImportError, ModuleNotFoundError):
    event_logger = None
try:
    from . import world_model
except (ImportError, ModuleNotFoundError):
    world_model = None
try:
    from . import kernel
except (ImportError, ModuleNotFoundError):
    kernel = None
try:
    from . import exceptions
except (ImportError, ModuleNotFoundError):
    exceptions = None
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

@dataclass
class TelemetryData:
    """Snapshot of cognitive and system health at a point in time."""
    timestamp: str
    cpu_load: float
    ram_percent: float
    disk_percent: float
    planner_depth: int
    memory_count: int
    memory_health: float
    event_throughput: float
    task_latency_avg: float
    recovery_frequency: float
    active_goals: int
    thread_count: int
    cognitive_stability_score: float
_CPU_COUNT: int = os.cpu_count() or 1
_ISO_FORMAT: str = '%Y-%m-%dT%H:%M:%S.%fZ'

def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime(_ISO_FORMAT)

def _read_proc_loadavg() -> float:
    """Return 1-minute CPU load average as a fraction (0..1+)."""
    try:
        with open('/proc/loadavg') as f:
            parts = f.read().split()
        return float(parts[0]) / _CPU_COUNT
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return 0.0

def _read_proc_meminfo() -> float:
    """Return RAM usage as a percentage (0–100)."""
    try:
        with open('/proc/meminfo') as f:
            raw = f.read()
        total = _parse_meminfo_value(raw, 'MemTotal:')
        available = _parse_meminfo_value(raw, 'MemAvailable:')
        if total > 0:
            return (1.0 - available / total) * 100.0
        return 0.0
    except (FileNotFoundError, OSError, ValueError):
        return 0.0

def _parse_meminfo_value(text: str, key: str) -> float:
    for line in text.splitlines():
        if line.startswith(key):
            parts = line.split()
            if len(parts) >= 2:
                return float(parts[1])
    return 0.0

def _disk_usage_percent(path: str='/') -> float:
    """Return disk usage as a percentage (0–100) for the given mount."""
    try:
        st = os.statvfs(path)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bfree
        if total > 0:
            return (1.0 - free / total) * 100.0
        return 0.0
    except (FileNotFoundError, OSError):
        return 0.0

def _safe_call(fn, default: Any=0.0) -> Any:
    """Call a function, returning *default* if it raises."""
    try:
        return fn()
    except Exception:
        return default
STABILITY_ACTIONS = {0.9: {'action': 'normal', 'max_depth': 5, 'max_parallel': 5, 'description': 'Fully operational'}, 0.7: {'action': 'caution', 'max_depth': 3, 'max_parallel': 3, 'description': 'Reduced capacity'}, 0.5: {'action': 'restricted', 'max_depth': 2, 'max_parallel': 1, 'description': 'Restricted mode'}, 0.3: {'action': 'safety', 'max_depth': 1, 'max_parallel': 0, 'description': 'Safety mode — no new tasks'}}

def _resolve_stability_action(score: float) -> dict:
    """Return the applicable stability action dict for a given score (0–1).

    Iterates thresholds in descending order and returns the first match.
    """
    for threshold in sorted(STABILITY_ACTIONS.keys(), reverse=True):
        if score >= threshold:
            return {'score': score, 'level': threshold, **STABILITY_ACTIONS[threshold]}
    lowest = min(STABILITY_ACTIONS.keys())
    return {'score': score, 'level': lowest, **STABILITY_ACTIONS[lowest]}

class Telemetry:
    """Singleton that periodically collects cognitive + system metrics.

    Usage::

        tm = Telemetry()
        tm.start(interval_s=30)
        # ...
        latest = tm.get_latest()
        print(latest.cognitive_stability_score)
        tm.stop()
    """
    _instance: Optional['Telemetry'] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> 'Telemetry':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    cls._instance = obj
        return cls._instance

    def __init__(self, data_dir: Optional[str]=None) -> None:
        if hasattr(self, '_initialised'):
            return
        self._initialised = True
        self._data_dir: str = data_dir or os.path.expanduser('~/.hermes/data/telemetry')
        os.makedirs(self._data_dir, exist_ok=True)
        self._history: list[dict[str, Any]] = []
        self._max_history: int = 1000
        self._latest: Optional[TelemetryData] = None
        self._interval_s: float = 60.0
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._stop_event: threading.Event = threading.Event()

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with cls._lock:
            cls._instance = None

    def start(self, interval_s: float=60.0) -> None:
        """Start background telemetry collection every *interval_s* seconds."""
        if self._running:
            return
        self._interval_s = max(1.0, interval_s)
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._collection_loop, name='hermes-telemetry', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background collection loop."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def record_tool_attempt(self, tool_name: str='', **_: Any) -> None:
        """Record that a tool invocation was attempted."""
        self._append_history({'type': 'tool_attempt', 'tool': tool_name, 'timestamp': _iso_now()})

    def record_tool(self, tool_name: str='', success: bool=True, **_: Any) -> None:
        """Record that a tool invocation completed."""
        self._append_history({'type': 'tool_result', 'tool': tool_name, 'success': success, 'timestamp': _iso_now()})

    def record_llm_latency(self, duration_s: float=0.0, **_: Any) -> None:
        """Record LLM call latency in seconds."""
        self._append_history({'type': 'llm_latency', 'duration_s': duration_s, 'timestamp': _iso_now()})

    def collect(self) -> TelemetryData:
        """Gather current system and cognitive metrics into a snapshot."""
        cpu_load = _read_proc_loadavg()
        ram_percent = _read_proc_meminfo()
        disk_percent = _disk_usage_percent()
        planner_depth = self._get_planner_depth()
        memory_count = self._get_memory_count()
        memory_health = self._get_memory_health()
        event_throughput = self._get_event_throughput()
        task_latency_avg = self._get_task_latency_avg()
        recovery_frequency = self._get_recovery_frequency()
        active_goals = self._get_active_goals()
        thread_count = threading.active_count()
        stability = self._compute_stability(ram_percent=ram_percent, planner_depth=planner_depth, memory_health=memory_health, recovery_frequency=recovery_frequency, disk_percent=disk_percent)
        data = TelemetryData(timestamp=_iso_now(), cpu_load=cpu_load, ram_percent=ram_percent, disk_percent=disk_percent, planner_depth=planner_depth, memory_count=memory_count, memory_health=memory_health, event_throughput=event_throughput, task_latency_avg=task_latency_avg, recovery_frequency=recovery_frequency, active_goals=active_goals, thread_count=thread_count, cognitive_stability_score=stability)
        self._latest = data
        self._append_history(asdict(data))
        prev_score = getattr(self, '_prev_stability_score', None)
        if prev_score is not None and abs(stability - prev_score) > 0.1:
            self.auto_adjust()
        self._prev_stability_score = stability
        return data

    def get_latest(self) -> Optional[TelemetryData]:
        """Return the most recent TelemetryData snapshot, or *None*."""
        return self._latest

    def get_history(self, limit: int=100) -> list[dict[str, Any]]:
        """Return the *limit* most recent history entries as dicts."""
        return self._history[-limit:]

    def get_cognitive_stability_score(self) -> float:
        """Return the latest cognitive stability score, or 0.0."""
        if self._latest is not None:
            return self._latest.cognitive_stability_score
        return 0.0

    def get_health_score(self) -> float:
        """Return a simple composite health score based on latest data.

        Weighted equally across RAM, disk, memory health, and stability.
        """
        if self._latest is None:
            return 0.0
        lst = self._latest
        ram_ok = 1.0 - min(1.0, lst.ram_percent / 100.0)
        disk_ok = 1.0 - min(1.0, lst.disk_percent / 100.0)
        mem_ok = lst.memory_health
        stability = lst.cognitive_stability_score
        return (ram_ok + disk_ok + mem_ok + stability) / 4.0

    def get_health_summary(self) -> float:
        """Compatibility alias for get_health_score().

        Added to prevent ``AttributeError`` in scripts that call
        the old method name ``get_health_summary()``.
        """
        return self.get_health_score()

    def export_json(self, path: str) -> None:
        """Export the full telemetry history to a JSON file."""
        with open(path, 'w') as f:
            json.dump({'exported_at': _iso_now(), 'count': len(self._history), 'entries': self._history}, f, indent=2)

    def _append_history(self, entry: dict[str, Any]) -> None:
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def _get_planner_depth(self) -> int:
        if planner is not None:
            if hasattr(planner, 'get_depth'):
                return _safe_call(planner.get_depth, default=0)
            if hasattr(planner, 'depth'):
                return int(_safe_call(lambda: planner.depth, default=0))
        return 0

    def _get_memory_count(self) -> int:
        if memory_manager is not None:
            if hasattr(memory_manager, 'get_health'):
                health = _safe_call(memory_manager.get_health, default={})
                if isinstance(health, dict):
                    total = health.get('total_memories', {})
                    if isinstance(total, dict):
                        return sum(total.values())
                    if isinstance(total, (int, float)):
                        return int(total)
            if hasattr(memory_manager, 'count'):
                return _safe_call(memory_manager.count, default=0)
        return 0

    def _get_memory_health(self) -> float:
        if memory_manager is not None:
            if hasattr(memory_manager, 'get_health'):
                health = _safe_call(memory_manager.get_health, default={})
                if isinstance(health, dict):
                    score = health.get('hygiene_score', health.get('health', 1.0))
                    return float(score)
        return 1.0

    def _get_event_throughput(self) -> float:
        if event_bus is not None:
            if hasattr(event_bus, 'get_stats'):
                stats = _safe_call(event_bus.get_stats, default={})
                if isinstance(stats, dict):
                    for key in ('events_per_minute', 'throughput', 'event_rate'):
                        if key in stats:
                            return float(stats[key])
        return 0.0

    def _get_task_latency_avg(self) -> float:
        if event_bus is not None:
            if hasattr(event_bus, 'get_stats'):
                stats = _safe_call(event_bus.get_stats, default={})
                if isinstance(stats, dict):
                    for key in ('latency_avg_seconds', 'avg_latency', 'task_latency'):
                        if key in stats:
                            return float(stats[key])
        return 0.0

    def _get_recovery_frequency(self) -> float:
        if event_logger is not None:
            if hasattr(event_logger, 'get_recovery_frequency'):
                return _safe_call(event_logger.get_recovery_frequency, default=0.0)
        return 0.0

    def _get_active_goals(self) -> int:
        if goal_manager is not None:
            if hasattr(goal_manager, 'get_active_goals'):
                result = _safe_call(goal_manager.get_active_goals, default=0)
                if isinstance(result, (list, tuple, set)):
                    return len(result)
                return int(result)
        return 0

    @staticmethod
    def _compute_stability(ram_percent: float, planner_depth: int, memory_health: float, recovery_frequency: float, disk_percent: float) -> float:
        """Compute composite cognitive_stability_score (0–1).

        Formula::

            0.3 * (1 - min(1, ram_percent/100))   # memory pressure
          + 0.2 * (1 - min(1, planner_depth/5))    # planner convergence
          + 0.2 * memory_health                     # memory hygiene
          + 0.15 * (1 - min(1, recovery_freq/10))  # recovery stability
          + 0.15 * (1 - min(1, disk_percent/100))  # disk health
        """
        score = 0.3 * (1.0 - min(1.0, ram_percent / 100.0)) + 0.2 * (1.0 - min(1.0, planner_depth / 5.0)) + 0.2 * memory_health + 0.15 * (1.0 - min(1.0, recovery_frequency / 10.0)) + 0.15 * (1.0 - min(1.0, disk_percent / 100.0))
        return max(0.0, min(1.0, score))

    def _collection_loop(self) -> None:
        """Background thread: collect and store metrics at interval."""
        while self._running and (not self._stop_event.is_set()):
            try:
                data = self.collect()
                if event_logger is not None:
                    self._log_telemetry_event(data)
                if data.cognitive_stability_score < 0.5:
                    self._publish_alert(data)
            except Exception as exc:
                logger.debug('telemetry: _collection_loop: %s', exc)
            for _ in range(int(self._interval_s)):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _log_telemetry_event(self, data: TelemetryData) -> None:
        """Write a telemetry snapshot entry via EventLogger."""
        if not hasattr(event_logger, 'get_logger'):
            return
        try:
            logger = event_logger.get_logger()
            if hasattr(logger, 'log'):
                logger.log('telemetry.collected', {'cognitive_stability_score': data.cognitive_stability_score, 'cpu_load': data.cpu_load, 'ram_percent': data.ram_percent, 'disk_percent': data.disk_percent, 'memory_health': data.memory_health, 'active_goals': data.active_goals})
        except Exception as exc:
            logger.debug('telemetry: _log_telemetry_event: %s', exc)

    def _publish_alert(self, data: TelemetryData) -> None:
        """Emit a low-stability alert on the event bus."""
        if event_bus is None:
            return
        if not hasattr(event_bus, 'publish'):
            return
        try:
            event_bus.publish('telemetry.alert', {'severity': 'warning', 'message': f'Cognitive stability dropped to {data.cognitive_stability_score:.3f}', 'data': asdict(data)}, source='telemetry')
        except Exception as exc:
            logger.debug('telemetry: _publish_alert: %s', exc)

    def get_stability_level(self) -> dict:
        """Return current stability level based on latest score.

        Returns
        -------
        dict
            {score, level, action, max_depth, max_parallel, description}
            If no data collected yet, returns the 'normal' level.
        """
        score = self.get_cognitive_stability_score()
        return _resolve_stability_action(score)

    def get_planner_limits(self) -> dict:
        """Return planner limits based on current stability.

        Returns
        -------
        dict
            {max_depth, max_parallel, allow_long_tasks, allow_network}
        """
        level = self.get_stability_level()
        return {'max_depth': level['max_depth'], 'max_parallel': level['max_parallel'], 'allow_long_tasks': level['max_depth'] >= 3, 'allow_network': level['max_depth'] >= 2}

    def should_execute_task(self, priority: int) -> bool:
        """Can a task of given priority be executed under current stability?

        Rules
        -----
        - stability < 0.3 : only RECOVERY priority tasks (>= 100)
        - stability < 0.5 : RECOVERY + USER_TASK (>= 90)
        - stability < 0.7 : all except CLEANUP (> 10)
        - stability >= 0.7: all tasks allowed

        Returns
        -------
        bool
        """
        score = self.get_cognitive_stability_score()
        if score < 0.3:
            return priority >= 100
        if score < 0.5:
            return priority >= 90
        if score < 0.7:
            return priority > 10
        return True

    def get_recommendations(self) -> list[str]:
        """Return actionable suggestions based on current stability snapshot.

        Returns
        -------
        list[str]
            Human-readable recommendation strings.
        """
        recommendations: list[str] = []
        if self._latest is None:
            return recommendations
        data = self._latest
        if data.ram_percent > 80.0:
            recommendations.append(f'High memory usage ({data.ram_percent:.0f}%) — consider running cleanup tasks to free resources.')
        if data.cognitive_stability_score < 0.5:
            recommendations.append(f'Stability score is low ({data.cognitive_stability_score:.2f}) — reduce parallel task load and avoid starting new complex tasks.')
        if data.planner_depth > 3:
            recommendations.append(f'Planner depth is {data.planner_depth} — consider simplifying or breaking down complex tasks.')
        if data.recovery_frequency > 5.0:
            recommendations.append(f'Recovery frequency is high ({data.recovery_frequency:.1f}/hr) — investigate recurring failures or instabilities.')
        if data.disk_percent > 85.0:
            recommendations.append(f'Disk usage is high ({data.disk_percent:.0f}%) — clean up old logs or telemetry archives.')
        return recommendations

    def auto_adjust(self) -> dict:
        """Auto-adjust system behavior based on current stability.

        Called by the background collection loop when the stability score
        changes by more than 0.1.  Publishes adjustment events to the
        EventBus and returns a summary dict.

        Returns
        -------
        dict
            {previous_level, new_level, adjustments: [str]}
        """
        previous_level = getattr(self, '_prev_stability_level', None)
        current = self.get_stability_level()
        current_level = current['level']
        adjustments: list[str] = []
        if previous_level is not None and previous_level != current_level:
            limits = self.get_planner_limits()
            adjustments.append(f"Stability changed from {previous_level} to {current_level} — action: {current['action']}, max_depth={limits['max_depth']}, max_parallel={limits['max_parallel']}")
            if event_bus is not None and hasattr(event_bus, 'publish'):
                try:
                    event_bus.publish('telemetry.adjust', {'previous_level': previous_level, 'new_level': current_level, 'action': current['action'], 'limits': limits, 'recommendations': self.get_recommendations()}, source='telemetry')
                except Exception as exc:
                    logger.debug('telemetry: auto_adjust: %s', exc)
            if event_logger is not None and hasattr(event_logger, 'get_logger'):
                try:
                    logger = event_logger.get_logger()
                    if hasattr(logger, 'log'):
                        logger.log('telemetry.auto_adjust', {'previous_level': previous_level, 'new_level': current_level, 'action': current['action'], 'adjustments': adjustments})
                except Exception as exc:
                    logger.debug('telemetry: auto_adjust: %s', exc)
        elif previous_level is None:
            adjustments.append(f"Initial stability level: {current_level} ({current['action']})")
        self._prev_stability_level = current_level
        return {'previous_level': previous_level, 'new_level': current_level, 'adjustments': adjustments}

def get_telemetry() -> Telemetry:
    """Return the global Telemetry singleton."""
    return Telemetry()

def start_telemetry(interval_s: float=60.0) -> Telemetry:
    """Convenience: get singleton and start collection."""
    tm = get_telemetry()
    tm.start(interval_s=interval_s)
    return tm

def collect_telemetry() -> dict[str, Any]:
    """Convenience: collect one snapshot and return as a dict."""
    tm = get_telemetry()
    data = tm.collect()
    return asdict(data)

def get_stability_level() -> dict:
    """Return current stability level from the global Telemetry singleton.

    Returns
    -------
    dict
        {score, level, action, max_depth, max_parallel, description}
    """
    return get_telemetry().get_stability_level()

def get_planner_limits() -> dict:
    """Return planner limits based on current stability.

    Returns
    -------
    dict
        {max_depth, max_parallel, allow_long_tasks, allow_network}
    """
    return get_telemetry().get_planner_limits()

def should_execute(priority: int=50) -> bool:
    """Can a task of given priority be executed under current stability?

    Parameters
    ----------
    priority : int
        Task priority value (higher = more critical).
        Default 50 corresponds to PLANNED priority.

    Returns
    -------
    bool
    """
    return get_telemetry().should_execute_task(priority)

@atexit.register
def _stop_telemetry() -> None:
    try:
        inst = Telemetry._instance
        if inst is not None and hasattr(inst, '_running') and inst._running:
            inst.stop()
    except Exception as exc:
        logger.debug('telemetry: auto_adjust: %s', exc)