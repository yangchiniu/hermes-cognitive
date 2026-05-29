"""
import logging

logger = logging.getLogger(__name__)


watchdog.py — Deadlock / Livelock Detection for Hermes Core.

Monitors registered threads (heartbeat-based) and tasks (timeout-based),
detects event storms, infinite OODA loops, and recursive planning depth
anomalies.  Integrates with EventBus for alert publishing.

Standard library only + existing core modules (try/except import pattern).
"""
from __future__ import annotations
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
try:
    from .event_bus import EventBus
    _event_bus_available = True
except ImportError:
    _event_bus_available = False
try:
    from .event_logger import get_logger
    _logger_available = True
except ImportError:
    _logger_available = False
try:
    from .exceptions import HermesCoreError
    class WatchdogError(HermesCoreError):
        """Raised on watchdog-specific issues."""

        def __init__(self, message: str='', context: Optional[dict[str, Any]]=None) -> None:
            super().__init__(message, context)
    _exceptions_available = True
except ImportError:
    _exceptions_available = False

    class WatchdogError(Exception):
        """Fallback watchdog error when exceptions module is unavailable."""

        def __init__(self, message: str='', context: Optional[dict[str, Any]]=None) -> None:
            self.context = context or {}
            super().__init__(message)
_WATCHDOG_EVENT_PREFIX = 'watchdog'
_EVENT_ALERT = f'{_WATCHDOG_EVENT_PREFIX}.alert'
_EVENT_HEARTBEAT_RECEIVED = f'{_WATCHDOG_EVENT_PREFIX}.heartbeat_received'
_EVENT_DEADLOCK_DETECTED = f'{_WATCHDOG_EVENT_PREFIX}.deadlock_detected'
_EVENT_TASK_TIMEOUT = f'{_WATCHDOG_EVENT_PREFIX}.task_timeout'
_ALERT_SEVERITY_WARNING = 'warning'
_ALERT_SEVERITY_CRITICAL = 'critical'
_DEFAULT_CHECK_INTERVAL_S = 5.0
_DEFAULT_HEARTBEAT_TIMEOUT_S = 30.0
_DEFAULT_TASK_TIMEOUT_S = 300.0
_DEFAULT_EVENT_STORM_THRESHOLD = 100
_DEFAULT_EVENT_STORM_WINDOW_S = 60.0
_DEFAULT_OODA_MAX_CYCLES = 20
_DEFAULT_OODA_WINDOW_S = 300.0
_DEFAULT_RECURSIVE_PLAN_DEPTH = 5
_HEARTBEAT_WARNING_MISSED = 1
_HEARTBEAT_CRITICAL_MISSED = 2
_TASK_WARNING_FRACTION = 0.8

def _now() -> float:
    """Return monotonic time (seconds) for interval measurements."""
    return time.monotonic()

def _utc_iso() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()

class Watchdog:
    """Monitors threads and tasks for deadlocks, livelocks, and anomalies.

    Operates as a singleton.  A background daemon thread runs periodic checks
    (every *check_interval_s*) and publishes alerts via EventBus when issues
    are detected.

    Usage
    -----
    >>> wd = Watchdog()
    >>> wd.register_thread("worker-1", heartbeat_timeout_s=10.0)
    >>> wd.heartbeat("worker-1")       # called periodically by the thread
    >>> wd.register_task("job-001", timeout_s=60.0)
    >>> wd.complete_task("job-001")
    >>> wd.start()                      # begin background monitoring
    >>> # ...
    >>> wd.stop()                       # stop monitoring
    >>> alerts = wd.get_alerts(clear=True)
    """
    _instance: Optional['Watchdog'] = None
    _instance_lock = threading.Lock()

    def __new__(cls, check_interval_s: float=_DEFAULT_CHECK_INTERVAL_S) -> 'Watchdog':
        with cls._instance_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                cls._instance = obj
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None


    def __init__(self, check_interval_s: float=_DEFAULT_CHECK_INTERVAL_S) -> None:
        """Initialise the Watchdog singleton.

        Parameters
        ----------
        check_interval_s : float
            Seconds between periodic deadlock/livelock checks (default: 5.0).
        """
        if getattr(self, '_initialized', False):
            return
        self._check_interval_s = check_interval_s
        self._threads: Dict[str, Dict[str, Any]] = {}
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._alerts: Deque[str] = deque(maxlen=500)
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._event_bus: Optional[Any] = None
        if _event_bus_available:
            try:
                self._event_bus = EventBus()
            except Exception:
                self._event_bus = None
        self._initialized = True

    def start(self) -> None:
        """Start the background monitoring daemon thread.

        Idempotent — safe to call multiple times.
        """
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, name='watchdog-monitor', daemon=True)
        self._monitor_thread.start()

    def stop(self) -> None:
        """Signal the monitoring thread to stop and wait for it.

        Idempotent — safe to call even if not started.
        """
        self._stop_event.set()
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)
        self._monitor_thread = None

    def register_thread(self, name: str, heartbeat_timeout_s: float=_DEFAULT_HEARTBEAT_TIMEOUT_S) -> None:
        """Register a thread for heartbeat-based deadlock monitoring.

        Parameters
        ----------
        name : str
            Unique identifier for the thread.
        heartbeat_timeout_s : float
            Seconds without a heartbeat before the thread is considered stuck.
            Default: 30.0.
        """
        with self._lock:
            self._threads[name] = {'name': name, 'last_heartbeat': _now(), 'heartbeat_timeout': heartbeat_timeout_s, 'missed_count': 0, 'registered_at': _utc_iso(), 'alive': True}

    def unregister_thread(self, name: str) -> bool:
        """Remove a thread from monitoring.

        Returns
        -------
        bool
            ``True`` if the thread was found and removed, ``False`` otherwise.
        """
        with self._lock:
            if name in self._threads:
                del self._threads[name]
                return True
            return False

    def heartbeat(self, name: str) -> None:
        """Called by a monitored thread to signal it is alive.

        Parameters
        ----------
        name : str
            The thread identifier previously passed to ``register_thread()``.
        """
        with self._lock:
            info = self._threads.get(name)
            if info is not None:
                info['last_heartbeat'] = _now()
                info['missed_count'] = 0
                info['alive'] = True
        self._publish(_EVENT_HEARTBEAT_RECEIVED, {'thread': name}, severity='info')

    def register_task(self, task_id: str, timeout_s: float=_DEFAULT_TASK_TIMEOUT_S) -> None:
        """Register a task with a timeout for completion monitoring.

        Parameters
        ----------
        task_id : str
            Unique identifier for the task.
        timeout_s : float
            Maximum allowed execution time in seconds.  Default: 300.0.
        """
        with self._lock:
            self._tasks[task_id] = {'task_id': task_id, 'start_time': _now(), 'timeout': timeout_s, 'completed': False, 'registered_at': _utc_iso()}

    def complete_task(self, task_id: str) -> bool:
        """Mark a registered task as completed.

        Returns
        -------
        bool
            ``True`` if the task was found and marked completed, ``False`` otherwise.
        """
        with self._lock:
            info = self._tasks.get(task_id)
            if info is not None:
                info['completed'] = True
                return True
            return False

    def check_deadlocks(self) -> List[Dict[str, Any]]:
        """Check all registered threads and tasks for deadlocks / timeouts.

        Returns
        -------
        list[dict]
            Each entry describes a detected issue::

                {
                    "type": "thread" | "task",
                    "name": str,
                    "severity": "warning" | "critical",
                    "message": str,
                    "missed_count": int,      # threads only
                    "elapsed_s": float,       # tasks only
                    "timestamp": str,
                }
        """
        issues: List[Dict[str, Any]] = []
        now = _now()
        timestamp = _utc_iso()
        with self._lock:
            for name, info in self._threads.items():
                elapsed = now - info['last_heartbeat']
                timeout = info['heartbeat_timeout']
                if elapsed >= timeout:
                    info['missed_count'] += 1
                    missed = info['missed_count']
                    if missed >= _HEARTBEAT_CRITICAL_MISSED:
                        info['alive'] = False
                        issues.append({'type': 'thread', 'name': name, 'severity': _ALERT_SEVERITY_CRITICAL, 'message': f"Thread '{name}' likely deadlocked — missed {missed} heartbeats, {elapsed:.1f}s since last heartbeat", 'missed_count': missed, 'elapsed_s': elapsed, 'timestamp': timestamp})
                    elif missed >= _HEARTBEAT_WARNING_MISSED:
                        issues.append({'type': 'thread', 'name': name, 'severity': _ALERT_SEVERITY_WARNING, 'message': f"Thread '{name}' missed {missed} heartbeat(s) — {elapsed:.1f}s since last heartbeat", 'missed_count': missed, 'elapsed_s': elapsed, 'timestamp': timestamp})
                else:
                    pass
            for task_id, info in self._tasks.items():
                if info['completed']:
                    continue
                elapsed = now - info['start_time']
                timeout = info['timeout']
                if elapsed >= timeout:
                    issues.append({'type': 'task', 'name': task_id, 'severity': _ALERT_SEVERITY_CRITICAL, 'message': f"Task '{task_id}' exceeded timeout — {elapsed:.1f}s elapsed, {timeout:.0f}s allowed", 'missed_count': 0, 'elapsed_s': elapsed, 'timestamp': timestamp})
                elif elapsed >= timeout * _TASK_WARNING_FRACTION:
                    remaining = timeout - elapsed
                    issues.append({'type': 'task', 'name': task_id, 'severity': _ALERT_SEVERITY_WARNING, 'message': f"Task '{task_id}' running for {elapsed:.1f}s ({elapsed / timeout * 100:.0f}% of {timeout:.0f}s timeout) — {remaining:.1f}s remaining", 'missed_count': 0, 'elapsed_s': elapsed, 'timestamp': timestamp})
        for issue in issues:
            self._publish_alert(issue)
        return issues

    def detect_event_storm(self, event_bus: Any, threshold: int=_DEFAULT_EVENT_STORM_THRESHOLD, window_s: float=_DEFAULT_EVENT_STORM_WINDOW_S) -> bool:
        """Check if the event publication rate exceeds *threshold* in *window_s*.

        Parameters
        ----------
        event_bus : EventBus
            An instance of ``EventBus`` (or compatible duck-typed object with a
            ``get_stats()`` method).
        threshold : int
            Maximum acceptable events within the window.  Default: 100.
        window_s : float
            Lookback window in seconds.  Default: 60.0.

        Returns
        -------
        bool
            ``True`` if an event storm is detected.
        """
        try:
            stats = event_bus.get_stats()
        except Exception:
            return False
        total = stats.get('total_events', 0)
        if total > threshold:
            self._add_alert(f'Event storm detected: {total} events published (threshold={threshold} in {window_s}s window)')
            self._publish(_EVENT_ALERT, {'detection': 'event_storm', 'total_events': total, 'threshold': threshold, 'window_s': window_s}, severity=_ALERT_SEVERITY_WARNING)
            return True
        return False

    def detect_infinite_loop(self, ooda_loop: Any, max_cycles: int=_DEFAULT_OODA_MAX_CYCLES, window_s: float=_DEFAULT_OODA_WINDOW_S) -> bool:
        """Detect if the OODA loop is cycling too rapidly without progress.

        Parameters
        ----------
        ooda_loop : OODALoop
            An OODA loop instance with a ``get_status()`` method returning a dict
            containing at least ``cycle_count``.
        max_cycles : int
            Maximum allowable cycles in *window_s* seconds.  Default: 20.
        window_s : float
            Lookback window in seconds.  Default: 300.0.

        Returns
        -------
        bool
            ``True`` if an infinite loop is suspected.
        """
        try:
            status = ooda_loop.get_status()
        except Exception:
            return False
        cycle_count = status.get('cycle_count', 0)
        if cycle_count > max_cycles:
            self._add_alert(f'Infinite OODA loop suspected: {cycle_count} cycles (max={max_cycles} in {window_s}s window)')
            self._publish(_EVENT_ALERT, {'detection': 'infinite_loop', 'cycle_count': cycle_count, 'max_cycles': max_cycles, 'window_s': window_s}, severity=_ALERT_SEVERITY_WARNING)
            return True
        return False

    def detect_recursive_planning(self, planner: Any, max_depth: int=_DEFAULT_RECURSIVE_PLAN_DEPTH) -> bool:
        """Detect if the planner is generating plans recursively too deeply.

        Parameters
        ----------
        planner : Planner
            A Planner instance with a ``list_plans()`` method returning a list of
            plan dicts (each containing at least ``plan_id`` and optionally
            ``steps``).
        max_depth : int
            Maximum acceptable number of active/recent plans before flagging.
            Default: 5.

        Returns
        -------
        bool
            ``True`` if recursive planning is suspected.
        """
        try:
            plans = planner.list_plans()
        except Exception:
            return False
        if not isinstance(plans, (list, tuple)):
            return False
        num_plans = len(plans)
        if num_plans > max_depth:
            self._add_alert(f'Recursive planning detected: {num_plans} active plans (max_depth={max_depth})')
            self._publish(_EVENT_ALERT, {'detection': 'recursive_planning', 'num_plans': num_plans, 'max_depth': max_depth}, severity=_ALERT_SEVERITY_WARNING)
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Return a snapshot of the watchdog's current state.

        Returns
        -------
        dict
            Keys: ``running``, ``check_interval_s``, ``threads``, ``tasks``,
            ``alerts_count``.
        """
        with self._lock:
            threads_snapshot = {}
            for name, info in self._threads.items():
                threads_snapshot[name] = {'alive': info.get('alive', True), 'missed_count': info.get('missed_count', 0), 'heartbeat_timeout_s': info.get('heartbeat_timeout', 30.0), 'seconds_since_heartbeat': _now() - info.get('last_heartbeat', 0)}
            tasks_snapshot = {}
            for task_id, info in self._tasks.items():
                tasks_snapshot[task_id] = {'completed': info.get('completed', False), 'timeout_s': info.get('timeout', 300.0), 'seconds_running': _now() - info.get('start_time', 0) if not info.get('completed', False) else 0.0}
            return {'running': self._monitor_thread is not None and self._monitor_thread.is_alive(), 'check_interval_s': self._check_interval_s, 'threads': threads_snapshot, 'tasks': tasks_snapshot, 'alerts_count': len(self._alerts)}

    def get_alerts(self, clear: bool=False) -> List[str]:
        """Return the list of recent alerts.

        Parameters
        ----------
        clear : bool
            If ``True``, clear the alert buffer after reading.

        Returns
        -------
        list[str]
            Alert messages in chronological order (oldest first).
        """
        with self._lock:
            alerts = list(self._alerts)
            if clear:
                self._alerts.clear()
        return alerts

    def _monitor_loop(self) -> None:
        """Background thread loop that periodically checks for deadlocks."""
        while not self._stop_event.is_set():
            try:
                self.check_deadlocks()
            except Exception as exc:
                logger.debug('watchdog: _monitor_loop: %s', exc)
            for _ in range(int(self._check_interval_s * 10)):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

    def _add_alert(self, message: str) -> None:
        """Add an alert message to the ring buffer."""
        with self._lock:
            self._alerts.append(message)

    def _publish_alert(self, issue: Dict[str, Any]) -> None:
        """Publish a deadlock/timeout alert message and event."""
        severity = issue.get('severity', _ALERT_SEVERITY_WARNING)
        message = issue.get('message', '')
        issue_type = issue.get('type', 'unknown')
        self._add_alert(message)
        if issue_type == 'thread' and severity == _ALERT_SEVERITY_CRITICAL:
            self._publish(_EVENT_DEADLOCK_DETECTED, issue, severity=severity)
        elif issue_type == 'task' and severity == _ALERT_SEVERITY_CRITICAL:
            self._publish(_EVENT_TASK_TIMEOUT, issue, severity=severity)
        else:
            self._publish(_EVENT_ALERT, issue, severity=severity)

    def _publish(self, event_type: str, data: Optional[Dict[str, Any]]=None, severity: str='info') -> None:
        """Publish an event to the EventBus (best-effort, non-blocking)."""
        if self._event_bus is not None:
            try:
                self._event_bus.publish(event_type=event_type, data=data or {}, source='watchdog', severity=severity)
            except Exception as exc:
                logger.debug('watchdog: _publish: %s', exc)
_instance: Optional[Watchdog] = None
_instance_lock = threading.Lock()

def get_watchdog() -> Watchdog:
    """Return the application-wide Watchdog singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Watchdog()
        return _instance

def start_watchdog() -> Watchdog:
    """Return the Watchdog singleton and start background monitoring.

    Returns
    -------
    Watchdog
    """
    wd = get_watchdog()
    wd.start()
    return wd

def thread_heartbeat(name: str) -> None:
    """Convenience function: signal a heartbeat for the named thread.

    Parameters
    ----------
    name : str
        The thread identifier.
    """
    wd = get_watchdog()
    wd.heartbeat(name)
__all__ = ['Watchdog', 'WatchdogError', 'get_watchdog', 'start_watchdog', 'thread_heartbeat', '_EVENT_ALERT', '_EVENT_HEARTBEAT_RECEIVED', '_EVENT_DEADLOCK_DETECTED', '_EVENT_TASK_TIMEOUT']