"""
self_observation.py — Periodic self-observation loop for Hermes Core.

Provides a singleton SelfObservationLoop that periodically checks system
health, resource status, task progress, event log health, memory pressure,
and recovery opportunities.  Generates ObservationReports and alerts on
critical findings.

Standard library only.  Try/except relative import pattern for dependencies.

Dependencies: runtime_supervisor.py, reflection_engine.py, world_model.py,
              memory_manager.py, event_logger.py, recovery_manager.py
"""
from __future__ import annotations
import threading
import time
import uuid as _uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
try:
    from .runtime_supervisor import get_supervisor, check_resources as _sup_check_resources
    from .world_model import get_world_model
    from .event_logger import get_logger
    from .memory_manager import get_memory_manager
    from .recovery_manager import get_recovery_manager
except ImportError:
    import sys as _sys
    import pathlib as _pathlib
    _pkg_dir = _pathlib.Path(__file__).resolve().parent
    if str(_pkg_dir) not in _sys.path:
        _sys.path.insert(0, str(_pkg_dir))
    from runtime_supervisor import get_supervisor, check_resources as _sup_check_resources
    from world_model import get_world_model
    from event_logger import get_logger
    from memory_manager import get_memory_manager
    from recovery_manager import get_recovery_manager
import logging

logger = logging.getLogger(__name__)
_STALE_TASK_THRESHOLD_SECONDS = 600
_ERROR_RATE_WINDOW_SECONDS = 300
_HIGH_MEMORY_THRESHOLD_PERCENT = 85
_HIGH_DISK_THRESHOLD_PERCENT = 90
_MAX_BROWSER_COUNT = 3
_MEMORY_CONSOLIDATION_THRESHOLD = 200
_FIRST_CHECK_DELAY_SECONDS = 10

@dataclass
class ObservationReport:
    """Structured result of a single observation cycle."""
    timestamp: str
    observation_id: str
    system_healthy: bool
    resource_status: dict
    incomplete_tasks: list
    event_log_stats: dict
    memory_stats: dict
    warnings: list[str]
    recommendations: list[str]
    auto_actions_taken: list[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

def _new_uuid() -> str:
    """Return a hex UUID string."""
    return str(_uuid.uuid4())

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()

def _parse_iso_timestamp(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp string to datetime."""
    try:
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)
_instance: Optional['SelfObservationLoop'] = None
_instance_lock = threading.Lock()

class SelfObservationLoop:
    """Periodic self-observation loop that monitors system health.

    Runs a background daemon thread that periodically checks all subsystems,
    generates ObservationReport instances, and raises alerts for critical
    issues.

    Usage::

        loop = SelfObservationLoop()
        loop.start(interval_s=300)
        ...
        last = loop.get_last_report()
        alerts = loop.get_pending_alerts()
        loop.stop()
    """

    def __init__(self) -> None:
        """Lazy init — no blocking I/O."""
        if getattr(self, '_initialized', False):
            return
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._last_report: Optional[ObservationReport] = None
        self._report_history: List[ObservationReport] = []
        self._max_history = 100
        self._alerts: List[str] = []
        self._alerts_lock = threading.Lock()
        self._initialized = True

    def start(self, interval_s: int=300) -> None:
        """Start the periodic self-observation loop.

        Parameters
        ----------
        interval_s : int
            Seconds between observation cycles (default: 300 = 5 minutes).
            The first check runs after a short delay (10 seconds).
        """
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._interval = max(interval_s, 10)
            self._running = True
            self._thread = threading.Thread(target=self._loop, args=(self._interval,), name='self-observation', daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Signal the observation loop to stop and wait for it to exit."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=10)

    def run_once(self) -> ObservationReport:
        """Execute a single observation cycle and return the report.

        Checks performed:
        a) System resources (via RuntimeSupervisor.check_resources())
        b) Incomplete/stale tasks (via WorldModel)
        c) Event log stats (via EventLogger.get_stats())
        d) Memory pressure (via MemoryManager.get_stats())
        e) Stale or failed operations (via RecoveryManager)
        f) Auto-remediation actions
        """
        obs_id = _new_uuid()
        warnings: List[str] = []
        recommendations: List[str] = []
        auto_actions: List[str] = []
        resource_status: Dict[str, Any] = {}
        try:
            sup = get_supervisor()
            resource_status = sup.check_resources()
        except Exception as exc:
            warnings.append(f'Resource check failed: {exc}')
            resource_status = {'healthy': False, 'error': str(exc)}
        incomplete_tasks: List[Dict[str, Any]] = []
        try:
            wm = get_world_model()
            stale_tasks = self._find_stale_tasks(wm)
            incomplete_tasks = stale_tasks
            if stale_tasks:
                warnings.append(f"Found {len(stale_tasks)} stale task(s) stuck in 'started' status for >{_STALE_TASK_THRESHOLD_SECONDS // 60} minutes")
                for t in stale_tasks:
                    task_id = t.get('id', '?')
                    desc = t.get('description', '?')
                    recommendations.append(f"Consider recovering or failing stale task {task_id} ('{desc[:60]}')")
        except Exception as exc:
            warnings.append(f'Task health check failed: {exc}')
        event_log_stats: Dict[str, Any] = {}
        try:
            logger = get_logger()
            event_log_stats = logger.get_stats()
        except Exception as exc:
            warnings.append(f'Event log stats failed: {exc}')
            event_log_stats = {'error': str(exc)}
        if isinstance(event_log_stats, dict):
            try:
                err_rate = self._compute_error_rate(event_log_stats)
                if err_rate > 0.5:
                    warnings.append(f'High error rate in event log: {err_rate:.1%} in last {_ERROR_RATE_WINDOW_SECONDS // 60} min')
            except Exception as exc:
                logger.debug('self_observation: run_once: %s', exc)
        memory_stats: Dict[str, Any] = {}
        try:
            mm = get_memory_manager()
            memory_stats = mm.get_stats()
        except Exception as exc:
            warnings.append(f'Memory stats failed: {exc}')
            memory_stats = {'error': str(exc)}
        if isinstance(memory_stats, dict):
            epi = memory_stats.get('episodic', {})
            epi_count = epi.get('count', 0) if isinstance(epi, dict) else 0
            if epi_count > _MEMORY_CONSOLIDATION_THRESHOLD:
                warnings.append(f'Episodic memory count ({epi_count}) exceeds consolidation threshold ({_MEMORY_CONSOLIDATION_THRESHOLD})')
                recommendations.append('Run memory consolidation to reduce episodic memory count')
                if epi_count > _MEMORY_CONSOLIDATION_THRESHOLD * 2:
                    try:
                        result = mm.consolidate()
                        merged = result.get('episodes_merged', 0)
                        pruned = result.get('semantic_pruned', 0)
                        if merged > 0 or pruned > 0:
                            msg = f'Auto-consolidation: merged {merged} episodes, pruned {pruned} facts'
                            auto_actions.append(msg)
                    except Exception as exc:
                        warnings.append(f'Auto-consolidation failed: {exc}')
        try:
            rm = get_recovery_manager()
            health = rm.check_health()
            if isinstance(health, dict):
                health_warnings = health.get('warnings', [])
                if isinstance(health_warnings, list):
                    for w in health_warnings:
                        if w not in warnings:
                            warnings.append(w)
                recoverable = health.get('recoverable_tasks', [])
                if isinstance(recoverable, list) and recoverable:
                    recommendations.append(f"{len(recoverable)} task(s) can be recovered: {[t.get('id', '?') for t in recoverable[:5]]}")
        except Exception as exc:
            warnings.append(f'Recovery health check failed: {exc}')
        if isinstance(resource_status, dict):
            ram_pct = resource_status.get('ram_percent', 0)
            if ram_pct > _HIGH_MEMORY_THRESHOLD_PERCENT:
                warnings.append(f'High memory usage: {ram_pct:.1f}% (threshold: {_HIGH_MEMORY_THRESHOLD_PERCENT}%)')
                recommendations.append('Consider running cache cleanup to reduce memory pressure')
            disk_pct = resource_status.get('disk_percent', 0)
            if disk_pct > _HIGH_DISK_THRESHOLD_PERCENT:
                warnings.append(f'High disk usage: {disk_pct:.1f}% (threshold: {_HIGH_DISK_THRESHOLD_PERCENT}%)')
                recommendations.append('Clean up temp files and old logs to free disk space')
            browser_count = resource_status.get('browser_count', 0)
            if browser_count > _MAX_BROWSER_COUNT:
                warnings.append(f'Browser count ({browser_count}) exceeds limit ({_MAX_BROWSER_COUNT})')
                recommendations.append('Consider closing stale browser processes')
        if isinstance(resource_status, dict):
            browser_count = resource_status.get('browser_count', 0)
            if browser_count > _MAX_BROWSER_COUNT * 2:
                self._add_alert(f'CRITICAL: {browser_count} browser processes running — system resource exhaustion risk')
        system_healthy = len(warnings) == 0
        report = ObservationReport(timestamp=_timestamp(), observation_id=obs_id, system_healthy=system_healthy, resource_status=dict(resource_status) if isinstance(resource_status, dict) else {'raw': str(resource_status)}, incomplete_tasks=incomplete_tasks, event_log_stats=dict(event_log_stats) if isinstance(event_log_stats, dict) else {'raw': str(event_log_stats)}, memory_stats=dict(memory_stats) if isinstance(memory_stats, dict) else {'raw': str(memory_stats)}, warnings=warnings, recommendations=recommendations, auto_actions_taken=auto_actions)
        with self._lock:
            self._last_report = report
            self._report_history.append(report)
            if len(self._report_history) > self._max_history:
                self._report_history = self._report_history[-self._max_history:]
        if not system_healthy:
            for w in warnings:
                self._add_alert(w)
        return report

    def get_last_report(self) -> Optional[ObservationReport]:
        """Return the most recent ObservationReport, or None."""
        with self._lock:
            return self._last_report

    def get_report_history(self, limit: int=10) -> List[ObservationReport]:
        """Return the last N observation reports."""
        with self._lock:
            return list(self._report_history[-limit:])

    def get_pending_alerts(self) -> List[str]:
        """Return all pending alerts and clear the buffer.

        Returns
        -------
        list of str
            Alert messages generated since the last call to ``clear_alerts()``.
        """
        with self._alerts_lock:
            alerts = list(self._alerts)
            self._alerts.clear()
        return alerts

    def clear_alerts(self) -> None:
        """Clear all pending alerts without reading them."""
        with self._alerts_lock:
            self._alerts.clear()

    def _loop(self, interval_s: int) -> None:
        """Background loop: run first check after delay, then periodically."""
        if self._stop_event.wait(timeout=_FIRST_CHECK_DELAY_SECONDS):
            return
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self._add_alert(f'Observation cycle failed: {exc}')
            if self._stop_event.wait(timeout=interval_s):
                break

    def _find_stale_tasks(self, wm) -> List[Dict[str, Any]]:
        """Find tasks stuck in 'started' status for >10 minutes.

        Uses WorldModel's internal DB connection to query task_history.
        """
        stale_tasks: List[Dict[str, Any]] = []
        try:
            conn = wm._mgr.get_connection('world_state')
            now = datetime.now(timezone.utc)
            cursor = conn.execute("SELECT id, session_id, task_type, description, status, started_at, completed_at, duration_seconds, result_summary, error_message FROM task_history WHERE status = 'started' OR status IS NULL ORDER BY started_at ASC")
            for row in cursor.fetchall():
                row_dict = dict(row)
                started_at = row_dict.get('started_at', '')
                if not started_at:
                    continue
                try:
                    started_dt = _parse_iso_timestamp(started_at)
                    elapsed = (now - started_dt).total_seconds()
                    if elapsed > _STALE_TASK_THRESHOLD_SECONDS:
                        row_dict['stale_seconds'] = elapsed
                        stale_tasks.append(row_dict)
                except (ValueError, TypeError):
                    continue
        except AttributeError:
            pass
        except Exception as exc:
            logger.debug('self_observation: _find_stale_tasks: %s', exc)
        return stale_tasks

    def _compute_error_rate(self, stats: Dict[str, Any]) -> float:
        """Compute the fraction of error/critical events in recent history.

        Reads from ``stats["by_severity"]`` counts.
        """
        by_severity = stats.get('by_severity', {})
        if not isinstance(by_severity, dict):
            return 0.0
        total = 0
        errors = 0
        for severity, count in by_severity.items():
            if isinstance(count, (int, float)):
                total += count
                if severity in ('error', 'critical'):
                    errors += count
        if total == 0:
            total_recent = stats.get('total_events', 0) or 0
            err_count = stats.get('error_count', 0) or 0
            if total_recent > 0:
                return err_count / total_recent
            return 0.0
        return errors / total

    def _add_alert(self, message: str) -> None:
        """Thread-safe add to alert buffer."""
        with self._alerts_lock:
            self._alerts.append(message)

def get_observer() -> SelfObservationLoop:
    """Return the application-wide SelfObservationLoop singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = SelfObservationLoop()
        return _instance


def reset_observer_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None
def start_observing(interval_s: int=300) -> SelfObservationLoop:
    """Convenience: get the observer singleton and start it.

    Parameters
    ----------
    interval_s : int
        Seconds between observation cycles (default: 300 = 5 minutes).

    Returns
    -------
    SelfObservationLoop
        The started singleton instance.
    """
    obs = get_observer()
    obs.start(interval_s=interval_s)
    return obs

def observe_once() -> dict:
    """Convenience: run a single observation cycle and return the report dict.

    Equivalent to ``get_observer().run_once().to_dict()``.
    """
    return get_observer().run_once().to_dict()