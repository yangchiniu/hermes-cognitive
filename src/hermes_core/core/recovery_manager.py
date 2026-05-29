"""
recovery_manager.py — Crash recovery for Hermes Core.

Provides a singleton RecoveryManager that checks system health, identifies
interrupted tasks, salvages session context from event logs, and saves/
loads checkpoints for crash resilience.

Standard library only.  Try/except relative import pattern for dependencies.

Dependencies: event_logger.py, world_model.py, task_graph.py, exceptions.py
"""
from __future__ import annotations
import json
import os
import pathlib
import sqlite3
import threading
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
try:
    from .event_logger import EventLogger, get_logger
    from .world_model import WorldModel, get_world_model
    from .task_graph import TaskGraphEngine, get_engine as get_task_graph_engine
    from .exceptions import RecoveryError
    from .db_schema import SchemaManager, DATA_DIR, DATABASE_SCHEMAS
except ImportError:
    import sys as _sys
    _pkg_dir = str(pathlib.Path(__file__).resolve().parent)
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from event_logger import EventLogger, get_logger
    from world_model import WorldModel, get_world_model
    from task_graph import TaskGraphEngine, get_engine as get_task_graph_engine
    from exceptions import RecoveryError
    from db_schema import SchemaManager, DATA_DIR, DATABASE_SCHEMAS
import logging

logger = logging.getLogger(__name__)
_DEFAULT_DATA_DIR = DATA_DIR
_MAX_SNAPSHOT_AGE_SECONDS = 300
_CHECKPOINT_EVENT_TYPE = 'recovery.checkpoint'
_HEALTH_EVENT_TYPE = 'recovery.health_check'
_RECOVERY_EVENT_TYPE = 'recovery.action'

def _new_uuid() -> str:
    """Return a fresh UUID4 hex string."""
    return str(_uuid.uuid4())

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None

def _seconds_since(ts: Optional[str]) -> Optional[float]:
    """Return seconds since *ts*, or None if unparseable."""
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds()
_instance: Optional['RecoveryManager'] = None
_instance_lock = threading.Lock()

class RecoveryManager:
    """Singleton crash recovery manager for Hermes Core.

    Performs health checks across all subsystems, identifies interrupted
    tasks and incomplete task graphs, salvages session context from event
    logs, and manages checkpoints for crash-resilient execution.

    Usage
    -----
    >>> rm = RecoveryManager()
    >>> health = rm.check_health()
    >>> plan = rm.recover_from_crash()
    >>> checkpoint_id = rm.save_checkpoint("task-42", "node-1", "milestone", {"progress": 50})
    """

    def __init__(self) -> None:
        if getattr(self, '_initialized', False):
            return
        self._data_dir = _DEFAULT_DATA_DIR
        self._schema_mgr: Optional[SchemaManager] = None
        self._logger: Optional[EventLogger] = None
        self._world_model: Optional[WorldModel] = None
        self._task_graph_engine: Optional[TaskGraphEngine] = None
        self._lock = threading.Lock()
        self._initialized = True

    def _get_schema_mgr(self) -> SchemaManager:
        if self._schema_mgr is None:
            self._schema_mgr = SchemaManager(self._data_dir)
        return self._schema_mgr

    def _get_logger(self) -> EventLogger:
        if self._logger is None:
            self._logger = get_logger(self._data_dir)
        return self._logger

    def _get_world_model(self) -> WorldModel:
        if self._world_model is None:
            self._world_model = get_world_model(self._data_dir)
        return self._world_model

    def _get_task_graph_engine(self) -> TaskGraphEngine:
        if self._task_graph_engine is None:
            self._task_graph_engine = get_task_graph_engine()
        return self._task_graph_engine

    def check_health(self) -> Dict[str, Any]:
        """Comprehensive health check across all subsystems.

        Checks performed:
        a) Database integrity (all known SQLite DBs)
        b) Event log integrity (parsability of NDJSON)
        c) World model last snapshot freshness
        d) Task graph incomplete tasks

        Returns
        -------
        dict
            Keys: ``healthy`` (bool), ``issues`` (list of str),
            ``warnings`` (list of str), ``last_check`` (ISO-8601 str).
        """
        issues: List[str] = []
        warnings: List[str] = []
        now = _timestamp()
        db_issues = self._check_databases()
        issues.extend(db_issues)
        log_issues = self._check_event_log()
        issues.extend(log_issues)
        snapshot_warning = self._check_snapshot_freshness()
        if snapshot_warning:
            warnings.append(snapshot_warning)
        graph_issues = self._check_incomplete_graphs()
        warnings.extend(graph_issues)
        healthy = len(issues) == 0
        try:
            self._get_logger().log(event_type=_HEALTH_EVENT_TYPE, data={'healthy': healthy, 'issues_count': len(issues), 'warnings_count': len(warnings)}, severity='warning' if not healthy else 'info')
        except Exception as exc:
            logger.debug('recovery_manager: check_health: %s', exc)
        return {'healthy': healthy, 'issues': issues, 'warnings': warnings, 'last_check': now}

    def _check_databases(self) -> List[str]:
        """Run integrity_check on all recognised SQLite databases.

        Returns a list of issue strings (empty = all healthy).
        """
        issues: List[str] = []
        mgr = self._get_schema_mgr()
        for db_name in DATABASE_SCHEMAS:
            db_path = mgr.db_path(db_name)
            if not db_path.exists():
                issues.append(f'Database file missing: {db_name}.db ({db_path})')
                continue
            try:
                conn = mgr.get_connection(db_name)
                cursor = conn.execute('PRAGMA integrity_check;')
                result = cursor.fetchone()
                if result and result[0] != 'ok':
                    issues.append(f'Integrity failure in {db_name}.db: {result[0]}')
            except sqlite3.Error as exc:
                issues.append(f'Cannot read {db_name}.db: {exc}')
            except Exception as exc:
                issues.append(f'Unexpected error checking {db_name}.db: {exc}')
            try:
                conn = mgr.get_connection(db_name)
                existing = set(mgr.list_tables(db_name))
                expected = set(DATABASE_SCHEMAS[db_name].keys())
                missing = expected - existing
                if missing:
                    issues.append(f'Tables missing in {db_name}.db: {sorted(missing)}')
            except Exception as exc:
                issues.append(f'Cannot list tables in {db_name}.db: {exc}')
        return issues

    def _check_event_log(self) -> List[str]:
        """Check that the event log NDJSON file is readable and parseable.

        Returns a list of issue strings.
        """
        issues: List[str] = []
        try:
            logger = self._get_logger()
            _ = logger.replay(limit=1)
        except Exception as exc:
            issues.append(f'Event log read/parse failure: {exc}')
        return issues

    def _check_snapshot_freshness(self) -> Optional[str]:
        """Check if the latest world model snapshot is fresh enough.

        Returns a warning string, or None if healthy.
        """
        try:
            wm = self._get_world_model()
            state = wm.get_world_state(refresh=False)
            snapshot_at = state.get('snapshot_at')
            if not snapshot_at:
                return 'No world model snapshot found (DB may be empty)'
            age = _seconds_since(snapshot_at)
            if age is None:
                return f'Cannot parse snapshot timestamp: {snapshot_at!r}'
            if age > _MAX_SNAPSHOT_AGE_SECONDS:
                return f'World model snapshot is {age:.0f}s old (threshold: {_MAX_SNAPSHOT_AGE_SECONDS}s)'
        except Exception as exc:
            return f'World model snapshot check failed: {exc}'
        return None

    def _check_incomplete_graphs(self) -> List[str]:
        """Check for task graphs that are still running/paused.

        Returns a list of warning strings.
        """
        warnings: List[str] = []
        try:
            engine = self._get_task_graph_engine()
            graph_ids = engine.list_graphs()
            for gid in graph_ids:
                try:
                    status = engine.get_graph_status(gid)
                    if status.get('status') in ('running', 'paused'):
                        name = status.get('name', gid)
                        incomplete = status.get('total_nodes', 0) - sum((c for s, c in status.get('counts', {}).items() if s in ('completed', 'failed', 'skipped')))
                        warnings.append(f"Graph {name!r} ({gid[:8]}...) is {status['status']} with {incomplete} incomplete nodes")
                except KeyError:
                    warnings.append(f'Graph {gid} not found in engine')
        except Exception as exc:
            warnings.append(f'Cannot check task graphs: {exc}')
        return warnings

    def recover_from_crash(self, session_id: Optional[str]=None) -> Dict[str, Any]:
        """THE main recovery method.

        Steps:
        a) Check health
        b) Find interrupted tasks (status='started' without completed_at)
        c) Find incomplete task graphs
        d) Determine what can be recovered vs what must be abandoned

        Parameters
        ----------
        session_id : str or None
            If provided, only consider tasks from this session.

        Returns
        -------
        dict
            Keys: ``healthy``, ``interrupted_tasks``, ``recoverable``,
            ``unrecoverable``, ``recommendations``, ``recovery_id``.
        """
        recovery_id = _new_uuid()
        health = self.check_health()
        interrupted = self._find_interrupted_tasks(session_id)
        incomplete_graphs = self._find_incomplete_graphs()
        recoverable: List[Dict[str, Any]] = []
        unrecoverable: List[Dict[str, Any]] = []
        for task in interrupted:
            task_id = task.get('id') or task.get('task_id')
            if not task_id:
                unrecoverable.append({**task, 'reason': 'No task ID'})
                continue
            has_checkpoints = self._has_checkpoints_for(task_id)
            if has_checkpoints:
                recoverable.append({'task_id': task_id, 'task_type': task.get('task_type', 'unknown'), 'description': task.get('description', ''), 'reason': 'Has checkpoints — recoverable', 'session_id': task.get('session_id')})
            else:
                unrecoverable.append({'task_id': task_id, 'task_type': task.get('task_type', 'unknown'), 'description': task.get('description', ''), 'reason': 'No checkpoints found — cannot resume', 'session_id': task.get('session_id')})
        for g in incomplete_graphs:
            graph_id = g.get('graph_id', '')
            has_ckpt = self._has_checkpoints_for(graph_id)
            entry = {'graph_id': graph_id, 'name': g.get('name', ''), 'status': g.get('status', ''), 'current_node': g.get('current_node'), 'has_checkpoints': has_ckpt}
            if has_ckpt:
                recoverable.append({'task_id': graph_id, 'task_type': 'task_graph', 'description': f"Graph {g.get('name', graph_id)}", 'reason': 'Graph has checkpoints — recoverable', 'graph_info': entry})
            else:
                unrecoverable.append({'task_id': graph_id, 'task_type': 'task_graph', 'description': f"Graph {g.get('name', graph_id)}", 'reason': 'No graph checkpoints — cannot resume', 'graph_info': entry})
        recommendations: List[str] = []
        if not health['healthy']:
            recommendations.append('Fix health issues before attempting recovery: ' + '; '.join(health['issues']))
        if recoverable:
            recommendations.append(f'Found {len(recoverable)} recoverable task(s) — call recover_task(<task_id>) to resume')
        if unrecoverable:
            recommendations.append(f'{len(unrecoverable)} task(s) are unrecoverable — they will need to be re-created')
        if not interrupted and (not incomplete_graphs):
            recommendations.append('No interrupted tasks or incomplete graphs found — system appears clean')
        try:
            self._get_logger().log(event_type=_RECOVERY_EVENT_TYPE, data={'recovery_id': recovery_id, 'interrupted_count': len(interrupted), 'recoverable_count': len(recoverable), 'unrecoverable_count': len(unrecoverable)}, severity='warning' if unrecoverable else 'info')
        except Exception as exc:
            logger.debug('recovery_manager: recover_from_crash: %s', exc)
        return {'healthy': health['healthy'], 'interrupted_tasks': interrupted, 'recoverable': recoverable, 'unrecoverable': unrecoverable, 'recommendations': recommendations, 'recovery_id': recovery_id}

    def _find_interrupted_tasks(self, session_id: Optional[str]=None) -> List[Dict[str, Any]]:
        """Query task_history for tasks started but never completed.

        Interrupted = status == 'started' AND completed_at IS NULL.
        """
        try:
            wm = self._get_world_model()
            mgr = self._get_schema_mgr()
            conn = mgr.get_connection('world_state')
            query = "SELECT id, session_id, task_type, description, status, started_at, completed_at, error_message FROM task_history WHERE status = 'started' AND completed_at IS NULL"
            params: List[Any] = []
            if session_id:
                query += ' AND session_id = ?'
                params.append(session_id)
            query += ' ORDER BY started_at DESC'
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            return []

    def _find_incomplete_graphs(self) -> List[Dict[str, Any]]:
        """Find task graphs that are still running, paused, or pending."""
        graphs: List[Dict[str, Any]] = []
        try:
            engine = self._get_task_graph_engine()
            for gid in engine.list_graphs():
                try:
                    status = engine.get_graph_status(gid)
                    if status.get('status') in ('running', 'paused', 'pending'):
                        graphs.append(status)
                except KeyError:
                    pass
        except Exception as exc:
            logger.debug('recovery_manager: _find_incomplete_graphs: %s', exc)
        return graphs

    def _has_checkpoints_for(self, task_or_graph_id: str) -> bool:
        """Check if any checkpoint events exist for *task_or_graph_id*."""
        try:
            logger = self._get_logger()
            events = logger.replay(event_types=[_CHECKPOINT_EVENT_TYPE], limit=1000)
            for ev in events:
                data = ev.get('data', {})
                if data.get('task_id') == task_or_graph_id:
                    return True
                if data.get('graph_id') == task_or_graph_id:
                    return True
        except Exception as exc:
            logger.debug('recovery_manager: _has_checkpoints_for: %s', exc)
        return False

    def recover_task(self, task_id: str) -> Dict[str, Any]:
        """Attempt to recover a specific task from its checkpoints.

        Steps:
        a) Check if task has checkpoints in the event log
        b) Load task graph if exists
        c) Find last completed node
        d) Return resume point + available context

        Parameters
        ----------
        task_id : str
            The task ID (from task_history or a graph_id).

        Returns
        -------
        dict
            Keys: ``recoverable`` (bool), ``resume_point`` (str or None),
            ``last_completed_node`` (str or None),
            ``checkpoints`` (list of dict), ``graph_state`` (dict or None),
            ``available_context`` (dict), ``message`` (str).
        """
        result: Dict[str, Any] = {'recoverable': False, 'resume_point': None, 'last_completed_node': None, 'checkpoints': [], 'graph_state': None, 'available_context': {}, 'message': ''}
        try:
            checkpoints = self._get_checkpoints_for(task_id)
            if not checkpoints:
                result['message'] = f'No checkpoints found for task/graph {task_id}'
                return result
            result['checkpoints'] = checkpoints
            result['recoverable'] = True
            resume_point = None
            last_completed_node = None
            latest_context: Dict[str, Any] = {}
            for ckpt in reversed(checkpoints):
                data = ckpt.get('data', {})
                state_type = data.get('state_type', '')
                node_id = data.get('node_id')
                context = data.get('context', {})
                if state_type == 'completed' or state_type == 'milestone':
                    if node_id:
                        if last_completed_node is None:
                            last_completed_node = node_id
                    latest_context.update(context)
                if resume_point is None and state_type not in ('completed', 'failed'):
                    resume_point = {'node_id': node_id, 'state_type': state_type, 'data_snapshot': context}
            result['resume_point'] = resume_point
            result['last_completed_node'] = last_completed_node
            result['available_context'] = latest_context
            try:
                engine = self._get_task_graph_engine()
                graph_state = engine.load_checkpoint(task_id)
                if graph_state:
                    result['graph_state'] = graph_state
                    current = graph_state.get('current_node')
                    if current:
                        result['resume_point'] = {'node_id': current, 'state_type': 'running', 'data_snapshot': graph_state}
            except (KeyError, Exception):
                pass
            if result['resume_point']:
                result['message'] = f"Task {task_id} is recoverable. Resume point: node {result['resume_point'].get('node_id')}"
            else:
                result['message'] = f'Task {task_id} has checkpoints but no clear resume point found — may need manual inspection'
        except Exception as exc:
            result['message'] = f'Error recovering task {task_id}: {exc}'
        return result

    def _get_checkpoints_for(self, task_id: str) -> List[Dict[str, Any]]:
        """Return all checkpoint events for *task_id* (newest-first)."""
        try:
            logger = self._get_logger()
            events = logger.replay(event_types=[_CHECKPOINT_EVENT_TYPE], limit=10000)
            return [ev for ev in events if ev.get('data', {}).get('task_id') == task_id or ev.get('data', {}).get('graph_id') == task_id]
        except Exception:
            return []

    def salvage_session(self, session_id: str) -> Dict[str, Any]:
        """Salvage context from a crashed session.

        Replays all events for that session and extracts:
        - last_goal
        - completed_steps
        - failed_steps
        - partial_results

        Parameters
        ----------
        session_id : str
            The session UUID to salvage.

        Returns
        -------
        dict
            Keys: ``session_id``, ``event_count``, ``last_goal`` (str or None),
            ``completed_steps`` (list), ``failed_steps`` (list),
            ``partial_results`` (list of dict), ``salvage_id`` (str).
        """
        salvage_id = _new_uuid()
        completed_steps: List[str] = []
        failed_steps: List[str] = []
        partial_results: List[Dict[str, Any]] = []
        last_goal: Optional[str] = None
        try:
            logger = self._get_logger()
            events = logger.get_session_events(session_id)
            for ev in reversed(events):
                event_type = ev.get('event_type', '')
                data = ev.get('data', {})
                if event_type == 'task.started' or event_type == 'goal.set':
                    goal = data.get('description') or data.get('goal')
                    if goal:
                        last_goal = goal
                if event_type == 'task.started' and (not last_goal):
                    last_goal = data.get('description', '')
                if event_type == 'task.updated':
                    status = data.get('status')
                    task_desc = data.get('task_id', '')
                    if status == 'completed':
                        completed_steps.append(task_desc)
                    elif status == 'failed':
                        failed_steps.append(task_desc)
                if event_type == _CHECKPOINT_EVENT_TYPE:
                    ckpt_data = data
                    if ckpt_data.get('state_type') in ('milestone', 'partial'):
                        partial_results.append({'node_id': ckpt_data.get('node_id'), 'task_id': ckpt_data.get('task_id'), 'state_type': ckpt_data.get('state_type'), 'context': ckpt_data.get('context', {}), 'timestamp': ev.get('timestamp')})
                if event_type == 'task_graph.checkpoint.node':
                    node_status = data.get('status')
                    if node_status == 'completed':
                        completed_steps.append(data.get('node_id', 'unknown'))
                    elif node_status == 'failed':
                        failed_steps.append(data.get('node_id', 'unknown'))
        except Exception as exc:
            return {'session_id': session_id, 'event_count': 0, 'last_goal': None, 'completed_steps': [], 'failed_steps': [], 'partial_results': [], 'salvage_id': salvage_id, 'error': str(exc)}
        return {'session_id': session_id, 'event_count': len(events) if 'events' in dir() else 0, 'last_goal': last_goal, 'completed_steps': completed_steps, 'failed_steps': failed_steps, 'partial_results': partial_results, 'salvage_id': salvage_id}

    def get_checkpoint(self, task_id: Optional[str]=None, graph_id: Optional[str]=None) -> Optional[Dict[str, Any]]:
        """Return the most recent checkpoint for a task or graph.

        Parameters
        ----------
        task_id : str or None
            Look up checkpoints for this task ID.
        graph_id : str or None
            Look up checkpoints for this graph ID.

        Returns
        -------
        dict or None
            The most recent checkpoint event, or None if none found.
        """
        lookup_id = task_id or graph_id
        if not lookup_id:
            return None
        checkpoints = self._get_checkpoints_for(lookup_id)
        if not checkpoints:
            return None
        return checkpoints[0]

    def save_checkpoint(self, task_id: str, node_id: str, state_type: str, data: Dict[str, Any]) -> str:
        """Save a checkpoint to the event log.

        Parameters
        ----------
        task_id : str
            The task or graph ID this checkpoint belongs to.
        node_id : str
            The specific node/step ID within the task.
        state_type : str
            One of ``"milestone"``, ``"partial"``, ``"completed"``,
            ``"failed"``, ``"running"``.
        data : dict
            Arbitrary JSON-serialisable context to associate with this
            checkpoint (e.g. progress, intermediate results).

        Returns
        -------
        str
            The event ID (UUID) of the saved checkpoint.
        """
        valid_types = {'milestone', 'partial', 'completed', 'failed', 'running'}
        if state_type not in valid_types:
            raise ValueError(f'Invalid state_type {state_type!r}. Must be one of {sorted(valid_types)}')
        payload: Dict[str, Any] = {'task_id': task_id, 'node_id': node_id, 'state_type': state_type, 'context': data}
        try:
            logger = self._get_logger()
            event_id = logger.log(event_type=_CHECKPOINT_EVENT_TYPE, data=payload, severity='warning' if state_type == 'failed' else 'info')
            return event_id
        except Exception as exc:
            raise RecoveryError(f'Failed to save checkpoint for task {task_id}: {exc}')

    def list_recoverable(self) -> List[Dict[str, Any]]:
        """List all tasks that could be recovered.

        Scans the task_history DB for interrupted tasks and checks if
        any have associated checkpoints in the event log.

        Returns
        -------
        list of dict
            Each entry contains task metadata and recovery eligibility.
        """
        recoverable: List[Dict[str, Any]] = []
        try:
            mgr = self._get_schema_mgr()
            conn = mgr.get_connection('world_state')
            cursor = conn.execute("SELECT id, session_id, task_type, description, status, started_at, error_message FROM task_history WHERE status = 'started' AND completed_at IS NULL ORDER BY started_at DESC")
            rows = cursor.fetchall()
            for row in rows:
                task_id = str(row['id'])
                has_ckpt = self._has_checkpoints_for(task_id)
                recoverable.append({'task_id': task_id, 'session_id': row['session_id'], 'task_type': row['task_type'], 'description': row['description'], 'status': row['status'], 'started_at': row['started_at'], 'recoverable': has_ckpt, 'checkpoints_found': has_ckpt})
        except Exception as exc:
            logger.debug('recovery_manager: list_recoverable: %s', exc)
        try:
            engine = self._get_task_graph_engine()
            for gid in engine.list_graphs():
                try:
                    status = engine.get_graph_status(gid)
                    if status.get('status') in ('running', 'paused', 'pending'):
                        has_ckpt = self._has_checkpoints_for(gid)
                        recoverable.append({'task_id': gid, 'graph_id': gid, 'graph_name': status.get('name', ''), 'task_type': 'task_graph', 'description': f"Graph {status.get('name', gid)}", 'status': status.get('status', 'unknown'), 'recoverable': has_ckpt, 'checkpoints_found': has_ckpt})
                except KeyError:
                    pass
        except Exception as exc:
            logger.debug('recovery_manager: list_recoverable: %s', exc)
        return recoverable

    def get_recovery_plan(self) -> Dict[str, Any]:
        """Return a full recovery plan for the current state.

        Combines health check, interrupted tasks, recoverable/unrecoverable
        classification, and task-specific recovery points into a single
        actionable plan.

        Returns
        -------
        dict
            Keys: ``plan_id``, ``timestamp``, ``health`` (dict),
            ``recoverable_tasks`` (list), ``unrecoverable_tasks`` (list),
            ``salvageable_sessions`` (list of str),
            ``total_recoverable`` (int), ``total_unrecoverable`` (int),
            ``recommended_actions`` (list of str).
        """
        plan_id = _new_uuid()
        health = self.check_health()
        crash_plan = self.recover_from_crash()
        salvageable_sessions: List[str] = []
        try:
            logger = self._get_logger()
            events = logger.replay(limit=5000)
            seen_sessions: set[str] = set()
            for ev in events:
                sid = ev.get('session_id')
                if sid and sid not in seen_sessions:
                    seen_sessions.add(sid)
                    salvageable_sessions.append(sid)
        except Exception as exc:
            logger.debug('recovery_manager: get_recovery_plan: %s', exc)
        recommended_actions: List[str] = []
        if not health['healthy']:
            recommended_actions.append('Step 1: Address health issues before any recovery attempts')
        if crash_plan['recoverable']:
            recommended_actions.append(f"Step 2: Call recover_task() for each of the {len(crash_plan['recoverable'])} recoverable task(s)")
        if salvageable_sessions:
            recommended_actions.append(f'Step 3: Call salvage_session() on {len(salvageable_sessions)} session(s) to extract context')
        if crash_plan['unrecoverable']:
            recommended_actions.append(f"Step 4: Re-create {len(crash_plan['unrecoverable'])} unrecoverable task(s) manually")
        if not crash_plan['interrupted_tasks']:
            recommended_actions.append('No action needed — no interrupted tasks found')
        return {'plan_id': plan_id, 'timestamp': _timestamp(), 'health': health, 'recoverable_tasks': crash_plan['recoverable'], 'unrecoverable_tasks': crash_plan['unrecoverable'], 'salvageable_sessions': salvageable_sessions, 'total_recoverable': len(crash_plan['recoverable']), 'total_unrecoverable': len(crash_plan['unrecoverable']), 'recommended_actions': recommended_actions}

def get_recovery_manager() -> RecoveryManager:
    """Return the application-wide ``RecoveryManager`` singleton.

    Lazily initialised on first call.
    """
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = RecoveryManager()
        return _instance


def reset_recovery_manager_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None


def check_health() -> Dict[str, Any]:
    """Convenience: check health of all subsystems.

    Equivalent to ``get_recovery_manager().check_health()``.
    """
    return get_recovery_manager().check_health()