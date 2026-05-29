"""
event_logger.py — Structured event log backed by SQLite (performance.db).

Singleton EventLogger that writes structured events to the ``events`` table in
``performance.db`` via ``SchemaManager``.  Thread-safe, pure stdlib.

Replaces the old NDJSON-based logger.  Uses the same public API:
    logger = get_logger()
    eid = logger.log("tool.call", {"tool": "search"}, severity="info")
    events = logger.replay(event_types=["tool.call"], limit=10)
"""
from __future__ import annotations
import json
import os
import pathlib
import threading
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = pathlib.Path.home() / '.hermes' / 'core' / 'data'
_EVENTS_DB = 'world_state'  # maps to performance.db via _DB_FILE_MAP

_VALID_SEVERITIES = frozenset({'info', 'warning', 'error', 'critical'})
_instances: dict[str, 'EventLogger'] = {}
_instances_lock = threading.Lock()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _session_id() -> str:
    sid = os.environ.get('HERMES_SESSION_ID')
    if sid is None:
        sid = _new_uuid()
        os.environ['HERMES_SESSION_ID'] = sid
    return sid


def _resolve_log_dir(log_dir: Optional[pathlib.Path]) -> pathlib.Path:
    if log_dir is not None:
        return pathlib.Path(log_dir).expanduser().resolve()
    return _DEFAULT_LOG_DIR


class EventLogger:
    """Append-only structured event logger (singleton per log_dir).

    Backed by the ``events`` table in ``performance.db`` (via SchemaManager).
    Thread-safe, auto-creates the table on first use.

    Usage
    -----
    >>> logger = EventLogger()
    >>> eid = logger.log("tool_call", {"tool": "search"})
    >>> events = logger.replay(event_types=["tool_call"], limit=10)
    """

    def __new__(cls, log_dir: Optional[pathlib.Path] = None) -> 'EventLogger':
        resolved = _resolve_log_dir(log_dir)
        key = str(resolved)
        with _instances_lock:
            if key not in _instances:
                obj = super().__new__(cls)
                obj._initialized = False
                _instances[key] = obj
            return _instances[key]

    def __init__(self, log_dir: Optional[pathlib.Path] = None) -> None:
        if getattr(self, '_initialized', False):
            return
        self._log_dir = _resolve_log_dir(log_dir)
        self._lock = threading.Lock()
        self._closed = False
        self._session_id = _session_id()
        self._schema_mgr: Any = None
        self._connection: Any = None
        self._pending_commits: int = 0
        self._batch_size: int = 10  # commit every N inserts

        # Lazily initialise SchemaManager — won't block if core modules
        # aren't available yet.
        self._init_schema()
        self._initialized = True


    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None

    def _init_schema(self) -> None:
        """Create the events table via SchemaManager if it doesn't exist."""
        try:
            from db_schema import get_manager

            self._schema_mgr = get_manager()
            self._schema_mgr.initialize(_EVENTS_DB)
            self._connection = self._schema_mgr.get_connection(_EVENTS_DB)
        except Exception as exc:
            logger.debug(
                "event_logger: SchemaManager init failed (will retry): %s", exc
            )
            self._connection = None

    def _ensure_conn(self) -> Any:
        """Return a SQLite connection, attempting lazy init if needed."""
        if self._connection is None:
            self._init_schema()
        return self._connection

    def log(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
        severity: str = 'info',
    ) -> str:
        """Write one event to the log and return the *event_id* (UUID).

        Parameters
        ----------
        event_type : str
            A dot-separated or flat category string, e.g. ``"tool.call"``.
        data : dict or None
            Arbitrary JSON-serialisable payload.  Defaults to ``{}``.
        severity : str
            One of ``"info"``, ``"warning"``, ``"error"``, ``"critical"``.

        Returns
        -------
        str
            The UUID assigned to this event.
        """
        if self._closed:
            raise RuntimeError('EventLogger has been closed')
        severity = severity.lower()
        if severity not in _VALID_SEVERITIES:
            raise ValueError(
                f'Invalid severity {severity!r}. '
                f'Must be one of {sorted(_VALID_SEVERITIES)}'
            )
        event_id = _new_uuid()
        record = {
            'event_id': event_id,
            'event_type': event_type,
            'severity': severity,
            'session_id': self._session_id,
            'data_json': json.dumps(
                data if data is not None else {}, ensure_ascii=False, default=str
            ),
            'created_at': _timestamp(),
        }

        with self._lock:
            if self._closed:
                raise RuntimeError('EventLogger has been closed')
            conn = self._ensure_conn()
            if conn is None:
                # Fallback: log to Python logger so events aren't lost
                logger.debug(
                    "event_logger: no SQLite connection, logging to std logger: %s/%s",
                    event_type,
                    event_id,
                )
                return event_id
            try:
                conn.execute(
                    """INSERT INTO events (event_id, event_type, severity,
                                           session_id, data_json, created_at)
                       VALUES (:event_id, :event_type, :severity,
                               :session_id, :data_json, :created_at)""",
                    record,
                )
                self._pending_commits += 1
                if self._pending_commits >= self._batch_size:
                    conn.commit()
                    self._pending_commits = 0
            except Exception as exc:
                logger.debug(
                    "event_logger: write failed: %s (event_id=%s)", exc, event_id
                )
        return event_id

    def flush(self) -> None:
        """Force-commit any pending batched writes."""
        with self._lock:
            if self._connection is not None and self._pending_commits > 0:
                try:
                    self._connection.commit()
                    self._pending_commits = 0
                except Exception as exc:
                    logger.debug("event_logger: flush failed: %s", exc)

    def replay(
        self,
        event_types: Optional[List[str]] = None,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Replay events from the SQLite log, newest-first.

        Parameters
        ----------
        event_types : list of str or None
            If provided, only return events whose ``event_type`` matches one of
            these values (SQL ``IN`` filter).
        since : str or None
            ISO-8601 timestamp; only return events on or after this time.
        limit : int
            Maximum number of events to return (default 100).

        Returns
        -------
        list of dict
            Each dict has keys: event_id, event_type, severity, session_id,
            data, created_at.
        """
        conn = self._ensure_conn()
        if conn is None:
            return []

        clauses: List[str] = []
        params: Dict[str, Any] = {}

        if event_types:
            placeholders = ','.join(f':et{i}' for i in range(len(event_types)))
            clauses.append(f'event_type IN ({placeholders})')
            for i, et in enumerate(event_types):
                params[f'et{i}'] = et

        if since:
            clauses.append('created_at >= :since')
            params['since'] = since

        where = ''
        if clauses:
            where = 'WHERE ' + ' AND '.join(clauses)

        sql = f'SELECT * FROM events {where} ORDER BY id DESC LIMIT :limit'
        params['limit'] = limit

        try:
            rows = conn.execute(sql, params).fetchall()
            result = []
            for row in rows:
                event = dict(row)
                # Parse data_json back to dict
                try:
                    event['data'] = json.loads(event.pop('data_json', '{}'))
                except (json.JSONDecodeError, TypeError):
                    event['data'] = {}
                result.append(event)
            return result
        except Exception as exc:
            logger.debug("event_logger: replay failed: %s", exc)
            return []

    def close(self) -> None:
        """Close the logger and release the database connection."""
        with self._lock:
            self._closed = True
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

    def __enter__(self) -> 'EventLogger':
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_instance: Optional[EventLogger] = None
_instance_lock = threading.Lock()


def get_logger(
    log_dir: Optional[pathlib.Path] = None,
) -> EventLogger:
    """Return the application-wide EventLogger singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = EventLogger(log_dir)
    return _instance
