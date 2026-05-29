"""
state_manager.py — State snapshot management for Hermes Core.

Manages world state snapshots (capture, restore, diff, export, cleanup)
on top of the WorldModel's system-state machinery.  Snapshots are stored
in the ``world_state.db`` database and logged to the NDJSON event log.

Standard library only: os, json, uuid, subprocess, pathlib, datetime, sqlite3, typing.
"""
from __future__ import annotations
import json
import os
import subprocess
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
try:
    from .world_model import WorldModel
    from .event_logger import EventLogger, get_logger
    from .exceptions import WorldStateError, HermesCoreError
    from .db_schema import SchemaManager, DATA_DIR
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from world_model import WorldModel
    from event_logger import EventLogger, get_logger
    from exceptions import WorldStateError, HermesCoreError
    from db_schema import SchemaManager, DATA_DIR
import logging

logger = logging.getLogger(__name__)
_DEFAULT_DATA_DIR = DATA_DIR
_SNAPSHOT_TABLE = 'state_snapshots'
_SNAPSHOT_SCHEMA = f"\n    CREATE TABLE IF NOT EXISTS {_SNAPSHOT_TABLE} (\n        id              TEXT PRIMARY KEY,\n        snapshot_at     TEXT NOT NULL,\n        description     TEXT DEFAULT '',\n        cwd             TEXT,\n        system_state    TEXT,\n        processes       TEXT,\n        network_info    TEXT,\n        raw_state       TEXT\n    )\n"
_HERMES_PROCESS_KEYWORDS = ('hermes', 'python', 'agent', 'hermes_')
_instance: Optional['StateManager'] = None

def get_state_manager(data_dir: Optional[Path]=None) -> 'StateManager':
    """Return the application-wide StateManager singleton."""
    global _instance
    if _instance is None:
        _instance = StateManager(data_dir)
    elif data_dir is not None:
        resolved = Path(data_dir).expanduser().resolve()
        if _instance._data_dir != resolved:
            _instance = StateManager(data_dir)
    return _instance

def capture() -> Dict[str, Any]:
    """Shorthand: capture a state snapshot and return the snapshot dict.

    Convenience wrapper around ``get_state_manager().capture_state()``
    that also returns the full state dict rather than just the id.
    """
    mgr = get_state_manager()
    snap_id = mgr.capture_state(description='adhoc capture')
    return mgr.restore_state(snap_id)

class StateManager:
    """Singleton managing world state snapshots and state transitions.

    Usage
    -----
    >>> mgr = StateManager()
    >>> snap_id = mgr.capture_state("before upgrade")
    >>> state = mgr.restore_state(snap_id)
    >>> history = mgr.get_state_history(limit=5)
    >>> diff = mgr.diff_states(snap_id_a, snap_id_b)
    >>> mgr.cleanup(keep_last=50)
    """

    def __init__(self, data_dir: Optional[Path]=None) -> None:
        self._data_dir = Path(data_dir).expanduser().resolve() if data_dir else _DEFAULT_DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._mgr = SchemaManager(self._data_dir)
        self._mgr.initialize('world_state')
        self._ensure_snapshot_table()
        self._world_model: Optional[WorldModel] = None
        self._logger: Optional[EventLogger] = None

    def capture_state(self, description: str='periodic snapshot') -> str:
        """Capture a full state snapshot and persist it.

        Steps
        -----
        1.  Calls ``WorldModel.snapshot()`` for system state (CPU, RAM, disk,
            network, browsers, active task).
        2.  Captures the current working directory.
        3.  Captures active processes filtered to hermes-related ones.
        4.  Captures network info (proxy env vars, internet check).
        5.  Generates a UUID snapshot id.
        6.  Persists to ``world_state.db`` (``state_snapshots`` table).
        7.  Logs via ``EventLogger``.
        8.  Returns the snapshot id.

        Parameters
        ----------
        description : str
            Human-readable label for the snapshot (default: ``"periodic
            snapshot"``).

        Returns
        -------
        str
            The snapshot UUID.
        """
        snapshot_id = self._new_id()
        now = self._timestamp()
        wm = self._get_world_model()
        system_state: Dict[str, Any] = {}
        try:
            system_state = wm.snapshot()
        except Exception as exc:
            system_state = {'error': str(exc)}
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = ''
        processes = self._capture_processes()
        network_info = self._capture_network_info()
        raw_state: Dict[str, Any] = {'snapshot_id': snapshot_id, 'snapshot_at': now, 'description': description, 'cwd': cwd, 'system_state': system_state, 'processes': processes, 'network_info': network_info}
        conn = self._mgr.get_connection('world_state')
        conn.execute(f'INSERT OR REPLACE INTO {_SNAPSHOT_TABLE}\n               (id, snapshot_at, description, cwd, system_state,\n                processes, network_info, raw_state)\n               VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (snapshot_id, now, description, cwd, json.dumps(system_state, ensure_ascii=False, default=str), json.dumps(processes, ensure_ascii=False, default=str), json.dumps(network_info, ensure_ascii=False, default=str), json.dumps(raw_state, ensure_ascii=False, default=str)))
        conn.commit()
        try:
            logger = self._get_logger()
            logger.log('state.snapshot', {'snapshot_id': snapshot_id, 'description': description, 'cwd': cwd, 'process_count': len(processes.get('hermes_processes', [])), 'network_status': network_info.get('status', 'unknown')})
        except Exception as exc:
            logger.debug('state_manager: capture_state: %s', exc)
        return snapshot_id

    def restore_state(self, snapshot_id: Optional[str]=None) -> Dict[str, Any]:
        """Load a snapshot from the database and return its full state dict.

        This is a **read-only** operation — it does not change any system
        state (no chdir, no process manipulation, etc.).

        Parameters
        ----------
        snapshot_id : str or None
            The UUID of the snapshot to load.  If ``None``, the most recent
            snapshot is returned.

        Returns
        -------
        dict
            The full raw state dict (keys: ``snapshot_id``, ``snapshot_at``,
            ``description``, ``cwd``, ``system_state``, ``processes``,
            ``network_info``).

        Raises
        ------
        WorldStateError
            If the requested snapshot does not exist or no snapshots exist.
        """
        conn = self._mgr.get_connection('world_state')
        if snapshot_id is not None:
            cursor = conn.execute(f'SELECT * FROM {_SNAPSHOT_TABLE} WHERE id = ?', (snapshot_id,))
        else:
            cursor = conn.execute(f'SELECT * FROM {_SNAPSHOT_TABLE} ORDER BY rowid DESC LIMIT 1')
        row = cursor.fetchone()
        if row is None:
            msg = f'Snapshot {snapshot_id!r} not found' if snapshot_id else 'No snapshots exist in the database'
            raise WorldStateError(msg)
        return self._row_to_state(row)

    def get_state_history(self, limit: int=10) -> List[Dict[str, Any]]:
        """Return a list of recent snapshots, newest first.

        Parameters
        ----------
        limit : int
            Maximum number of snapshots to return (default: 10).

        Returns
        -------
        list[dict]
            Each dict contains snapshot metadata (``snapshot_id``,
            ``snapshot_at``, ``description``, ``cwd``).  The full
            ``system_state`` and ``raw_state`` are **not** included for
            brevity — call ``restore_state()`` for the full payload.
        """
        conn = self._mgr.get_connection('world_state')
        cursor = conn.execute(f'SELECT id, snapshot_at, description, cwd\n                FROM {_SNAPSHOT_TABLE}\n                ORDER BY snapshot_at DESC\n                LIMIT ?', (limit,))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def diff_states(self, snapshot_id_a: str, snapshot_id_b: str) -> Dict[str, Any]:
        """Compare two snapshots and return their differences.

        Parameters
        ----------
        snapshot_id_a : str
            UUID of the first (older) snapshot.
        snapshot_id_b : str
            UUID of the second (newer) snapshot.

        Returns
        -------
        dict
            Keys:
            - ``memory_diff`` — changes in memory usage
            - ``disk_diff`` — changes in disk usage
            - ``tasks_diff`` — changes in active task
            - ``process_diff`` — changes in hermes processes
            - ``cwd_diff`` — working directory changes
            - ``network_diff`` — network status changes
            - ``summary`` — human-readable summary string

        Raises
        ------
        WorldStateError
            If either snapshot does not exist.
        """
        state_a = self.restore_state(snapshot_id_a)
        state_b = self.restore_state(snapshot_id_b)
        sys_a = state_a.get('system_state', {})
        sys_b = state_b.get('system_state', {})
        mem_a = sys_a.get('memory', {})
        mem_b = sys_b.get('memory', {})
        memory_diff: Dict[str, Any] = {}
        for key in ('total_mb', 'used_mb', 'available_mb', 'percent'):
            va = mem_a.get(key)
            vb = mem_b.get(key)
            if va is not None or vb is not None:
                memory_diff[key] = {'from': va, 'to': vb}
        disk_a = sys_a.get('disk', {})
        disk_b = sys_b.get('disk', {})
        disk_diff: Dict[str, Any] = {}
        for key in ('total_gb', 'used_gb', 'free_gb', 'percent'):
            va = disk_a.get(key)
            vb = disk_b.get(key)
            if va is not None or vb is not None:
                disk_diff[key] = {'from': va, 'to': vb}
        tasks_diff: Dict[str, Any] = {}
        task_a = sys_a.get('active_task')
        task_b = sys_b.get('active_task')
        if task_a != task_b:
            tasks_diff['active_task'] = {'from': task_a, 'to': task_b}
        procs_a = state_a.get('processes', {}).get('hermes_processes', [])
        procs_b = state_b.get('processes', {}).get('hermes_processes', [])
        process_diff: Dict[str, Any] = {'count_from': len(procs_a), 'count_to': len(procs_b)}
        pids_a = {p.get('pid') for p in procs_a}
        pids_b = {p.get('pid') for p in procs_b}
        new_pids = pids_b - pids_a
        gone_pids = pids_a - pids_b
        if new_pids:
            process_diff['new_processes'] = [p for p in procs_b if p.get('pid') in new_pids]
        if gone_pids:
            process_diff['ended_processes'] = [p for p in procs_a if p.get('pid') in gone_pids]
        cwd_a = state_a.get('cwd')
        cwd_b = state_b.get('cwd')
        cwd_diff = None
        if cwd_a != cwd_b:
            cwd_diff = {'from': cwd_a, 'to': cwd_b}
        net_a = state_a.get('network_info', {})
        net_b = state_b.get('network_info', {})
        network_diff: Dict[str, Any] = {}
        net_status_changed = net_a.get('status') != net_b.get('status')
        net_proxy_changed = net_a.get('proxy') != net_b.get('proxy')
        if net_status_changed:
            network_diff['status'] = {'from': net_a.get('status'), 'to': net_b.get('status')}
        if net_proxy_changed:
            network_diff['proxy'] = {'from': net_a.get('proxy'), 'to': net_b.get('proxy')}
        summary_parts: List[str] = []
        if memory_diff.get('percent'):
            mp = memory_diff['percent']
            summary_parts.append(f"RAM: {mp['from']}% -> {mp['to']}%")
        if disk_diff.get('percent'):
            dp = disk_diff['percent']
            summary_parts.append(f"Disk: {dp['from']}% -> {dp['to']}%")
        if tasks_diff.get('active_task'):
            summary_parts.append(f"Task changed: {tasks_diff['active_task']['from']} -> {tasks_diff['active_task']['to']}")
        if process_diff.get('new_processes'):
            summary_parts.append(f"{len(process_diff['new_processes'])} new process(es)")
        if process_diff.get('ended_processes'):
            summary_parts.append(f"{len(process_diff['ended_processes'])} ended process(es)")
        if cwd_diff:
            summary_parts.append('Working directory changed')
        if network_diff:
            summary_parts.append('Network status changed')
        if not summary_parts:
            summary_parts.append('No significant changes detected')
        return {'memory_diff': memory_diff, 'disk_diff': disk_diff, 'tasks_diff': tasks_diff, 'process_diff': process_diff, 'cwd_diff': cwd_diff, 'network_diff': network_diff, 'summary': '; '.join(summary_parts)}

    def export_state(self, snapshot_id: str, format: str='json') -> str:
        """Export a snapshot as a JSON string.

        Parameters
        ----------
        snapshot_id : str
            UUID of the snapshot to export.
        format : str
            Output format.  Only ``"json"`` is supported currently.

        Returns
        -------
        str
            The full snapshot state as a JSON string.

        Raises
        ------
        WorldStateError
            If the snapshot does not exist or the format is unsupported.
        """
        if format != 'json':
            raise WorldStateError(f"Unsupported export format {format!r}; only 'json' is supported")
        state = self.restore_state(snapshot_id)
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    def cleanup(self, keep_last: int=20) -> int:
        """Remove old snapshots, keeping only the *keep_last* most recent.

        Parameters
        ----------
        keep_last : int
            Number of most recent snapshots to retain (default: 20).

        Returns
        -------
        int
            Number of snapshots deleted.
        """
        conn = self._mgr.get_connection('world_state')
        cursor = conn.execute(f'SELECT id FROM {_SNAPSHOT_TABLE}\n                ORDER BY snapshot_at DESC\n                LIMIT ?', (keep_last,))
        keep_ids = {row['id'] for row in cursor.fetchall()}
        cursor = conn.execute(f'SELECT id FROM {_SNAPSHOT_TABLE}')
        all_ids = {row['id'] for row in cursor.fetchall()}
        delete_ids = all_ids - keep_ids
        if not delete_ids:
            return 0
        placeholders = ', '.join(('?' for _ in delete_ids))
        conn.execute(f'DELETE FROM {_SNAPSHOT_TABLE} WHERE id IN ({placeholders})', tuple(delete_ids))
        conn.commit()
        return len(delete_ids)

    def _ensure_snapshot_table(self) -> None:
        """Create the ``state_snapshots`` table if it does not exist."""
        conn = self._mgr.get_connection('world_state')
        conn.execute(_SNAPSHOT_SCHEMA)
        conn.commit()

    def _get_world_model(self) -> WorldModel:
        """Return (and lazily create) the WorldModel instance."""
        if self._world_model is None:
            self._world_model = WorldModel(self._data_dir)
        return self._world_model

    def _get_logger(self) -> EventLogger:
        """Return (and lazily create) the EventLogger singleton."""
        if self._logger is None:
            self._logger = get_logger(self._data_dir)
        return self._logger

    @staticmethod
    def _new_id() -> str:
        """Generate a new UUID string."""
        return str(_uuid.uuid4())

    @staticmethod
    def _timestamp() -> str:
        """Return ISO-8601 UTC timestamp string."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _capture_processes() -> Dict[str, Any]:
        """Capture hermes-related process info from ``ps aux``.

        Returns
        -------
        dict
            Keys:
            - ``hermes_processes`` — list of matching process dicts
            - ``total_processes`` — total count of all processes
        """
        result: Dict[str, Any] = {'hermes_processes': [], 'total_processes': 0}
        try:
            ps = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=10)
        except (subprocess.SubprocessError, OSError):
            return result
        lines = ps.stdout.splitlines()
        if not lines:
            return result
        header_line = lines[0].lower()
        header_parts = header_line.split()
        try:
            pid_idx = header_parts.index('pid')
            cpu_idx = header_parts.index('%cpu')
            mem_idx = header_parts.index('%mem')
            cmd_idx = header_parts.index('command')
        except ValueError:
            pid_idx = 1
            cpu_idx = 2
            mem_idx = 3
            cmd_idx = 10
        result['total_processes'] = len(lines) - 1
        for line in lines[1:]:
            parts = line.split(None, cmd_idx + 1)
            if len(parts) <= cmd_idx:
                continue
            cmd = parts[cmd_idx] if cmd_idx < len(parts) else ''
            cmd_lower = cmd.lower()
            if any((kw in cmd_lower for kw in _HERMES_PROCESS_KEYWORDS)):
                try:
                    pid = int(parts[pid_idx]) if pid_idx < len(parts) else 0
                except (ValueError, IndexError):
                    pid = 0
                try:
                    cpu_pct = float(parts[cpu_idx]) if cpu_idx < len(parts) else 0.0
                except (ValueError, IndexError):
                    cpu_pct = 0.0
                try:
                    mem_pct = float(parts[mem_idx]) if mem_idx < len(parts) else 0.0
                except (ValueError, IndexError):
                    mem_pct = 0.0
                result['hermes_processes'].append({'pid': pid, 'cpu_pct': cpu_pct, 'mem_pct': mem_pct, 'command': cmd})
        return result

    @staticmethod
    def _capture_network_info() -> Dict[str, Any]:
        """Capture network connectivity and proxy settings.

        Returns
        -------
        dict
            Keys: ``status`` (``"online"`` | ``"offline"``), ``proxy``
            (str or ``None``), ``proxy_vars`` (dict of all proxy env vars).
        """
        proxy_vars = {'http_proxy': os.environ.get('http_proxy'), 'https_proxy': os.environ.get('https_proxy'), 'HTTP_PROXY': os.environ.get('HTTP_PROXY'), 'HTTPS_PROXY': os.environ.get('HTTPS_PROXY'), 'no_proxy': os.environ.get('no_proxy'), 'NO_PROXY': os.environ.get('NO_PROXY')}
        active_proxy_vars = {k: v for k, v in proxy_vars.items() if v is not None}
        proxy = None
        for var in ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY'):
            val = os.environ.get(var)
            if val:
                proxy = val
                break
        online = False
        try:
            result = subprocess.run(['ping', '-c', '1', '-W', '3', '8.8.8.8'], capture_output=True, timeout=4)
            online = result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            pass
        return {'status': 'online' if online else 'offline', 'proxy': proxy, 'proxy_vars': active_proxy_vars}

    @staticmethod
    def _row_to_state(row: Any) -> Dict[str, Any]:
        """Convert a DB row to a full state dictionary, parsing JSON fields."""
        state: Dict[str, Any] = {'snapshot_id': row['id'], 'snapshot_at': row['snapshot_at'], 'description': row['description'], 'cwd': row['cwd']}
        json_fields = {'system_state': row['system_state'], 'processes': row['processes'], 'network_info': row['network_info'], 'raw_state': row['raw_state']}
        for key, raw in json_fields.items():
            if raw:
                try:
                    state[key] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    state[key] = {}
            else:
                state[key] = {}
        return state
__all__ = ['StateManager', 'get_state_manager', 'capture']