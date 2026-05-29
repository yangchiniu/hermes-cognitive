"""
runtime_supervisor.py — Runtime Supervisor for Hermes Core.

Monitors system resources (CPU, RAM, disk, browsers, active tasks),
generates alerts when thresholds are exceeded, and provides corrective
action suggestions.  Runs a background monitoring loop every 30 seconds.

Standard library only + module-level imports of world_model, policy_engine,
event_logger, exceptions.
"""
from __future__ import annotations
import os
import io
import json
import time
import threading
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
_world_model_mod = None
_policy_engine_mod = None
_event_logger_mod = None
_exceptions_mod = None
_import_lock = threading.Lock()

def _lazy_import_world():
    global _world_model_mod
    if _world_model_mod is None:
        with _import_lock:
            if _world_model_mod is None:
                try:
                    from . import world_model as _world_model_mod
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    import world_model as _world_model_mod


def reset__lazy_import_world_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None

def _lazy_import_policy():
    global _policy_engine_mod
    if _policy_engine_mod is None:
        with _import_lock:
            if _policy_engine_mod is None:
                try:
                    from . import policy_engine as _policy_engine_mod
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    import policy_engine as _policy_engine_mod

def _lazy_import_event():
    global _event_logger_mod
    if _event_logger_mod is None:
        with _import_lock:
            if _event_logger_mod is None:
                try:
                    from . import event_logger as _event_logger_mod
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    import event_logger as _event_logger_mod

def _lazy_import_exceptions():
    global _exceptions_mod
    if _exceptions_mod is None:
        with _import_lock:
            if _exceptions_mod is None:
                try:
                    from . import exceptions as _exceptions_mod
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    import exceptions as _exceptions_mod
import logging

logger = logging.getLogger(__name__)
_DEFAULT_THRESHOLDS: Dict[str, Any] = {'max_memory_percent': 85, 'max_disk_percent': 90, 'max_runtime_minutes': 20, 'max_browsers': 3, 'max_concurrent_tasks': 2, 'cpu_load_warn': 2.0}
_MONITOR_INTERVAL_SEC = 30
ResourceStatus = Dict[str, Any]
_instance: Optional['RuntimeSupervisor'] = None
_instance_lock = threading.Lock()

class RuntimeSupervisor:
    """Singleton runtime supervisor that monitors system resources.

    Usage::

        sup = RuntimeSupervisor()
        sup.start()
        status = sup.check_resources()
        alerts = sup.get_alerts()
        sup.stop()
    """

    def __init__(self) -> None:
        """Lazy init — no blocking I/O."""
        if getattr(self, '_initialized', False):
            return
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._thresholds: Dict[str, Any] = dict(_DEFAULT_THRESHOLDS)
        self._alerts: List[str] = []
        self._alerts_lock = threading.Lock()
        self._history: List[ResourceStatus] = []
        self._history_lock = threading.Lock()
        self._max_history = 50
        self._last_status: Optional[ResourceStatus] = None
        self._initialized = True

    def start(self) -> None:
        """Begin the monitoring loop in a background daemon thread."""
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._running = True
            self._monitor_thread = threading.Thread(target=self._monitor_loop, name='runtime-supervisor', daemon=True)
            self._monitor_thread.start()

    def stop(self) -> None:
        """Signal the monitor loop to stop and wait for it to exit."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
        t = self._monitor_thread
        if t and t.is_alive():
            t.join(timeout=10)

    def check_resources(self) -> ResourceStatus:
        """Take a single-point snapshot of system resources.

        Returns
        -------
        dict with keys:
            cpu_load, ram_available_mb, ram_percent, disk_percent,
            browser_count, task_count, task_duration_max_s,
            healthy, alerts, snapshot_at
        """
        self._ensure_thresholds()
        snapshot_at = _timestamp()
        alerts: List[str] = []
        cpu_load = self._read_cpu_load_1m()
        cpu_warn = self._thresholds.get('cpu_load_warn', 2.0)
        if cpu_load > cpu_warn:
            alerts.append(f'CPU load {cpu_load:.2f} exceeds warning threshold {cpu_warn:.2f}')
        mem = self._read_memory()
        ram_available_mb = mem.get('available_mb', 0.0)
        ram_percent = mem.get('percent', 0.0)
        max_mem = self._thresholds.get('max_memory_percent', 85)
        if ram_percent > max_mem:
            alerts.append(f'RAM usage {ram_percent:.1f}% exceeds limit {max_mem}%')
        disk_percent = self._read_disk_percent()
        max_disk = self._thresholds.get('max_disk_percent', 90)
        if disk_percent > max_disk:
            alerts.append(f'Disk usage {disk_percent:.1f}% exceeds limit {max_disk}%')
        browser_count = self._count_browsers()
        max_browsers = self._thresholds.get('max_browsers', 3)
        if browser_count > max_browsers:
            alerts.append(f'Browser count {browser_count} exceeds limit {max_browsers}')
        task_count = 0
        task_duration_max_s = 0.0
        try:
            _lazy_import_world()
            wm = _world_model_mod.get_world_model()
            summary = wm.get_summary()
            task_count = summary.get('active_tasks', 0)
            task_duration_max_s = self._query_max_task_duration(wm)
        except Exception as exc:
            logger.debug('runtime_supervisor: check_resources: %s', exc)
        max_tasks = self._thresholds.get('max_concurrent_tasks', 2)
        if task_count > max_tasks:
            alerts.append(f'Active task count {task_count} exceeds limit {max_tasks}')
        max_runtime_s = self._thresholds.get('max_runtime_minutes', 20) * 60
        if task_duration_max_s > max_runtime_s:
            alerts.append(f'Longest task runtime {task_duration_max_s:.0f}s exceeds limit {max_runtime_s:.0f}s')
        healthy = len(alerts) == 0
        for alert_msg in alerts:
            self._log_alert(alert_msg)
        result: ResourceStatus = {'cpu_load': round(cpu_load, 2), 'ram_available_mb': round(ram_available_mb, 1), 'ram_percent': round(ram_percent, 1), 'disk_percent': round(disk_percent, 1), 'browser_count': browser_count, 'task_count': task_count, 'task_duration_max_s': round(task_duration_max_s, 1), 'healthy': healthy, 'alerts': list(alerts), 'snapshot_at': snapshot_at}
        with self._alerts_lock:
            self._alerts.extend(alerts)
        with self._history_lock:
            self._history.append(result)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
        self._last_status = result
        try:
            _lazy_import_world()
            wm = _world_model_mod.get_world_model()
            wm.snapshot()
        except Exception as exc:
            logger.debug('runtime_supervisor: check_resources: %s', exc)
        return result

    def _monitor_loop(self) -> None:
        """Background loop: check resources every 30 seconds."""
        while not self._stop_event.is_set():
            try:
                self.check_resources()
            except Exception as exc:
                try:
                    _lazy_import_event()
                    logger = _event_logger_mod.get_logger()
                    logger.log('supervisor.error', {'error': str(exc)}, severity='error')
                except Exception as exc:
                    logger.debug('runtime_supervisor: _monitor_loop: %s', exc)
            self._stop_event.wait(_MONITOR_INTERVAL_SEC)

    def get_status(self) -> ResourceStatus:
        """Return the current supervisor status.

        If ``check_resources()`` has never been called, a fresh check is
        performed.  Otherwise the last cached status is returned.
        """
        if self._last_status is None:
            return self.check_resources()
        return dict(self._last_status)

    def get_history(self) -> List[ResourceStatus]:
        """Return the last N resource check snapshots (newest first)."""
        with self._history_lock:
            return list(reversed(self._history))

    def get_alerts(self, clear: bool=False) -> List[str]:
        """Return pending alerts.

        Parameters
        ----------
        clear : bool
            If ``True``, clear the alert buffer after reading.
        """
        with self._alerts_lock:
            alerts = list(self._alerts)
            if clear:
                self._alerts.clear()
        return alerts

    def handle_alert(self, alert: str) -> Optional[str]:
        """Auto-respond to a specific alert message.

        Returns a recommendation string or ``None``.
        """
        alert_lower = alert.lower()
        if 'browser' in alert_lower:
            msg = 'Too many browser instances. Consider killing unused browser processes: pkill -f firefox; pkill -f chromium-browser; pkill -f chrome'
            self._log_event('supervisor.corrective', {'action': msg})
            return msg
        if 'ram' in alert_lower or 'memory' in alert_lower:
            msg = 'High memory usage. Consider clearing system caches: echo 3 | sudo tee /proc/sys/vm/drop_caches  (admin only), or close memory-heavy applications.'
            self._log_event('supervisor.corrective', {'action': msg})
            return msg
        if 'task' in alert_lower:
            msg = 'Too many active tasks. Consider pausing or cancelling lower-priority tasks.'
            self._log_event('supervisor.corrective', {'action': msg})
            return msg
        if 'disk' in alert_lower:
            msg = 'High disk usage. Consider cleaning temporary files: rm -rf /tmp/* && sudo apt-get clean && find ~/.cache -type f -delete'
            self._log_event('supervisor.corrective', {'action': msg})
            return msg
        if 'cpu' in alert_lower:
            msg = 'High CPU load. Consider pausing background tasks or reducing concurrency.'
            self._log_event('supervisor.corrective', {'action': msg})
            return msg
        return None

    def force_cleanup(self) -> Dict[str, Any]:
        """Attempt to clean up system resources.

        Attempts:
        - Kill browser processes
        - Clean temp files (/tmp/*, ~/.cache/*)
        - Log what was done

        Returns a dict with keys ``actions_taken`` and ``errors``.
        """
        actions_taken: List[str] = []
        errors: List[str] = []
        try:
            browser_kws = ('chrom', 'chrome', 'firefox')
            for kw in browser_kws:
                result = subprocess.run(['pkill', '-f', kw], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    actions_taken.append(f"Killed processes matching '{kw}'")
        except (subprocess.SubprocessError, OSError) as exc:
            errors.append(f'Failed to kill browser processes: {exc}')
        temp_paths = ['/tmp/', os.path.expanduser('~/.cache/')]
        for path in temp_paths:
            if os.path.isdir(path):
                try:
                    subprocess.run(['find', path, '-type', 'f', '-atime', '+1', '-delete'], capture_output=True, timeout=30)
                    actions_taken.append(f'Cleaned old temp files in {path}')
                except (subprocess.SubprocessError, OSError) as exc:
                    errors.append(f'Failed to clean {path}: {exc}')
        result = {'actions_taken': actions_taken, 'errors': errors}
        self._log_event('supervisor.cleanup', result)
        return result

    def get_recommendations(self) -> List[str]:
        """Return actionable suggestions based on current resource state."""
        status = self.get_status()
        recs: List[str] = []
        if status.get('cpu_load', 0) > self._thresholds.get('cpu_load_warn', 2.0):
            recs.append(f"CPU load is {status['cpu_load']:.2f}. Pause background jobs or reduce concurrent processes.")
        mem_pct = status.get('ram_percent', 0)
        if mem_pct > self._thresholds.get('max_memory_percent', 85):
            recs.append(f'RAM at {mem_pct:.1f}%. Close unused applications or clear caches.')
        disk_pct = status.get('disk_percent', 0)
        if disk_pct > self._thresholds.get('max_disk_percent', 90):
            recs.append(f"Disk at {disk_pct:.1f}%. Run 'sudo apt-get clean', remove old logs, or delete temporary files.")
        bcount = status.get('browser_count', 0)
        if bcount > self._thresholds.get('max_browsers', 3):
            recs.append(f'{bcount} browser instances running. Consider killing unused browser tabs or processes.')
        tcount = status.get('task_count', 0)
        if tcount > self._thresholds.get('max_concurrent_tasks', 2):
            recs.append(f'{tcount} active tasks. Consider completing or cancelling lower-priority tasks.')
        dur = status.get('task_duration_max_s', 0)
        max_runtime_s = self._thresholds.get('max_runtime_minutes', 20) * 60
        if dur > max_runtime_s:
            recs.append(f'Longest-running task is {dur:.0f}s. Consider wrapping up long-running tasks.')
        return recs

    def _ensure_thresholds(self) -> None:
        """Load thresholds from policy engine config (falls back to defaults)."""
        try:
            _lazy_import_policy()
            engine = _policy_engine_mod.get_policy_engine()
            summary = engine.get_summary()
            limits = summary.get('limits', {})
            thresholds = dict(_DEFAULT_THRESHOLDS)
            key_map = {'max_memory_percent': 'max_memory_percent', 'max_disk_percent': 'max_disk_percent', 'max_runtime_minutes': 'max_runtime_minutes', 'max_parallel_browsers': 'max_browsers', 'max_concurrent_tasks': 'max_concurrent_tasks'}
            for policy_key, threshold_key in key_map.items():
                val = limits.get(policy_key)
                if val is not None:
                    thresholds[threshold_key] = val
            self._thresholds = thresholds
        except Exception as exc:
            logger.debug('runtime_supervisor: _ensure_thresholds: %s', exc)

    @staticmethod
    def _read_cpu_load_1m() -> float:
        """Read 1-minute CPU load average from /proc/loadavg."""
        try:
            with open('/proc/loadavg') as f:
                parts = f.read().strip().split()
            return float(parts[0])
        except (OSError, IndexError, ValueError):
            return 0.0

    @staticmethod
    def _read_memory() -> Dict[str, float]:
        """Read memory stats from /proc/meminfo.

        Returns dict with keys: total_mb, available_mb, used_mb, percent.
        """
        meminfo: Dict[str, int] = {}
        try:
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) != 2:
                        continue
                    key = parts[0].strip()
                    val_str = parts[1].strip().split()[0]
                    try:
                        meminfo[key] = int(val_str)
                    except (ValueError, IndexError):
                        pass
        except OSError:
            pass
        total_kb = meminfo.get('MemTotal', 0)
        available_kb = meminfo.get('MemAvailable', 0)
        total_mb = total_kb / 1024.0
        available_mb = available_kb / 1024.0
        used_mb = total_mb - available_mb
        percent = used_mb / total_mb * 100.0 if total_mb > 0 else 0.0
        return {'total_mb': round(total_mb, 1), 'available_mb': round(available_mb, 1), 'used_mb': round(used_mb, 1), 'percent': round(percent, 1)}

    @staticmethod
    def _read_disk_percent() -> float:
        """Read disk usage percentage for the root filesystem."""
        try:
            st = os.statvfs('/')
            total = st.f_frsize * st.f_blocks
            free = st.f_frsize * st.f_bfree
            if total > 0:
                return (total - free) / total * 100.0
        except (AttributeError, OSError):
            pass
        return 0.0

    @staticmethod
    def _count_browsers() -> int:
        """Count running browser processes via ``ps aux | grep``."""
        browser_keywords = ('chrom', 'chrome', 'firefox')
        try:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)
            count = 0
            for line in result.stdout.splitlines():
                lower = line.lower()
                for kw in browser_keywords:
                    if kw in lower:
                        count += 1
                        break
            return count
        except (subprocess.SubprocessError, OSError):
            return 0

    @staticmethod
    def _query_max_task_duration(wm) -> float:
        """Query the world model DB for the maximum duration of active tasks.

        Returns the max duration in seconds (0.0 if none or unavailable).
        """
        try:
            conn = wm._mgr.get_connection('world_state')
            cursor = conn.execute("SELECT started_at FROM task_history WHERE status NOT IN ('completed', 'failed') OR status IS NULL ORDER BY id DESC")
            now = datetime.now(timezone.utc)
            max_dur = 0.0
            for row in cursor:
                started_str = row['started_at']
                if started_str:
                    try:
                        started = datetime.fromisoformat(started_str)
                        dur = (now - started).total_seconds()
                        if dur > max_dur:
                            max_dur = dur
                    except (ValueError, TypeError):
                        pass
            return max_dur
        except Exception:
            return 0.0

    def _log_alert(self, alert_msg: str) -> None:
        """Log an alert to the event logger."""
        try:
            _lazy_import_event()
            logger = _event_logger_mod.get_logger()
            logger.log('supervisor.alert', {'message': alert_msg}, severity='warning')
        except Exception as exc:
            logger.debug('runtime_supervisor: _log_alert: %s', exc)

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log a generic supervisor event."""
        try:
            _lazy_import_event()
            logger = _event_logger_mod.get_logger()
            logger.log(event_type, data, severity='info')
        except Exception as exc:
            logger.debug('runtime_supervisor: _log_event: %s', exc)

    def __repr__(self) -> str:
        return f'RuntimeSupervisor(running={self._running}, alerts={len(self._alerts)}, history={len(self._history)})'

def get_supervisor() -> RuntimeSupervisor:
    """Return the application-wide RuntimeSupervisor singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = RuntimeSupervisor()
        return _instance

def check_resources() -> ResourceStatus:
    """Convenience: take a single resource snapshot.

    Equivalent to ``get_supervisor().check_resources()``.
    """
    return get_supervisor().check_resources()

def get_status() -> ResourceStatus:
    """Convenience: return the current supervisor status.

    Equivalent to ``get_supervisor().get_status()``.
    """
    return get_supervisor().get_status()

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()