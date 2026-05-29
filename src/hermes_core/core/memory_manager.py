"""
memory_manager.py — Multi-layer Memory System for Hermes Core.

Implements 5 layers of memory inspired by human cognitive architecture:

  1. Working Memory    — Current task context, transient (in-memory dict)
  2. Episodic Memory   — Past task episodes (persisted in SQLite)
  3. Semantic Memory   — Facts and knowledge (persisted in SQLite)
  4. Procedural Memory — How to do things (persisted in SQLite)
  5. Environment Memory — Computing environment info (persisted in SQLite)

Standard library only: sqlite3, uuid, datetime, json, pathlib, threading.
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
try:
    from .db_schema import SchemaManager, DATA_DIR
    from .event_logger import EventLogger, get_logger
    from .exceptions import HermesMemoryError as MemoryError
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from db_schema import SchemaManager, DATA_DIR
    from event_logger import EventLogger, get_logger
    from exceptions import HermesMemoryError as MemoryError
_DEFAULT_DATA_DIR = DATA_DIR
_DB_NAME = 'memory'
_MAX_RECENT_ACTIONS = 20
_EPISODE_CONSOLIDATION_THRESHOLD = 5
_SEMANTIC_PRUNE_CONFIDENCE = 0.1
MEMORY_SCHEMA: dict[str, str] = {'episodic_memories': '\n        CREATE TABLE IF NOT EXISTS episodic_memories (\n            id              INTEGER PRIMARY KEY AUTOINCREMENT,\n            session_id      TEXT NOT NULL,\n            description     TEXT NOT NULL,\n            summary         TEXT,\n            outcome         TEXT,\n            tags            TEXT,\n            created_at      TEXT NOT NULL,\n            access_count    INTEGER DEFAULT 0\n        )\n    ', 'semantic_facts': "\n        CREATE TABLE IF NOT EXISTS semantic_facts (\n            id              INTEGER PRIMARY KEY AUTOINCREMENT,\n            fact            TEXT NOT NULL,\n            category        TEXT DEFAULT 'general',\n            confidence      REAL DEFAULT 1.0,\n            source          TEXT,\n            created_at      TEXT NOT NULL,\n            last_accessed_at TEXT,\n            access_count    INTEGER DEFAULT 0\n        )\n    ", 'procedural_memories': '\n        CREATE TABLE IF NOT EXISTS procedural_memories (\n            id                  INTEGER PRIMARY KEY AUTOINCREMENT,\n            skill_name          TEXT NOT NULL,\n            trigger_conditions  TEXT,\n            steps               TEXT,\n            domain              TEXT,\n            success_count       INTEGER DEFAULT 0,\n            fail_count          INTEGER DEFAULT 0,\n            avg_duration_s      REAL DEFAULT 0.0,\n            last_used_at        TEXT\n        )\n    ', 'environment_facts': "\n        CREATE TABLE IF NOT EXISTS environment_facts (\n            id                  INTEGER PRIMARY KEY AUTOINCREMENT,\n            key                 TEXT UNIQUE NOT NULL,\n            value               TEXT,\n            category            TEXT DEFAULT 'system',\n            last_verified_at    TEXT,\n            source              TEXT\n        )\n    "}
try:
    from . import db_schema as _db_schema_mod
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    import db_schema as _db_schema_mod
import logging

logger = logging.getLogger(__name__)
_db_schema_mod.DATABASE_SCHEMAS[_DB_NAME] = MEMORY_SCHEMA

def _timestamp() -> str:
    """Return ISO-8601 UTC timestamp string."""
    return datetime.now(timezone.utc).isoformat()

def _new_uuid() -> str:
    """Return a hex UUID string."""
    return str(uuid.uuid4())

def _session_id() -> str:
    """Return the current session identifier from env or a fresh UUID."""
    sid = os.environ.get('HERMES_SESSION_ID')
    if sid is None:
        sid = _new_uuid()
        os.environ['HERMES_SESSION_ID'] = sid
    return sid

def _str_or_none(val: Any) -> Optional[str]:
    """Return a JSON-encoded string of *val*, or None if val is None."""
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False, default=str)

def _maybe_json_load(val: Optional[str]) -> Any:
    """Try to parse *val* as JSON; return the original string on failure."""
    if val is None:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return val

def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def _days_ago(iso_str: Optional[str]) -> int:
    """Return the number of days between *iso_str* (UTC ISO-8601) and now."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() / 86400))
    except (ValueError, TypeError):
        return 0

class MemoryManager:
    """Multi-layer memory system (singleton).

    Manages 5 memory layers:
      1. **Working** (in-memory dict) — transient task context
      2. **Episodic** (SQLite)        — past task episodes
      3. **Semantic** (SQLite)        — facts and knowledge
      4. **Procedural** (SQLite)      — skills / how-to knowledge
      5. **Environment** (SQLite)     — computing environment info

    Usage::

        mm = get_memory_manager()
        mm.set_focus("Implement parser module")
        mm.push_action("write_file", "File created")
        eid = mm.remember_episode("Task A", "Parsed input", "Success")
        fid = mm.learn_fact("Python 3.12 is latest", "programming")
        pid = mm.learn_procedure("parse_json", "need to parse json", "...")
        mm.update_env("OS", "Ubuntu 24.04", "system")
    """
    _instance: Optional['MemoryManager'] = None
    _instance_lock = threading.Lock()

    def __new__(cls, data_dir: Optional[Path]=None) -> 'MemoryManager':
        with cls._instance_lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._initialized = False
                obj._init_lock = threading.Lock()
                cls._instance = obj
            return cls._instance

    def __init__(self, data_dir: Optional[Path]=None) -> None:
        """Lazy initialisation — only runs once."""
        if getattr(self, '_initialized', False):
            return
        with self._init_lock:
            if self._initialized:
                return
            self._data_dir: Path = Path(data_dir).expanduser().resolve() if data_dir else _DEFAULT_DATA_DIR
            self._schema_mgr = SchemaManager(self._data_dir)
            self._schema_mgr.initialize(_DB_NAME)
            self._logger: Optional[EventLogger] = None
            self._working: Dict[str, Any] = {'active_task': None, 'current_focus': None, 'recent_actions': [], 'tool_call_stack': [], 'current_goal': None}
            self._semantic_index: Optional[Any] = None  # lazy SemanticRetrieval instance
            self._initialized = True


    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None

    def _get_conn(self) -> sqlite3.Connection:
        """Return the memory database connection."""
        return self._schema_mgr.get_connection(_DB_NAME)

    def _log_event(self, event_type: str, data: Dict[str, Any]) -> str:
        """Write a structured event to the NDJSON log."""
        logger = self._get_logger()
        return logger.log(event_type, data)

    def _get_logger(self) -> EventLogger:
        if self._logger is None:
            self._logger = get_logger(self._data_dir)
        return self._logger

    def set_focus(self, topic: str) -> None:
        """Set the current focus. Overwrites any previous focus."""
        old = self._working['current_focus']
        self._working['current_focus'] = topic
        self._log_event('memory.working.focus', {'old': old, 'new': topic})

    def get_focus(self) -> Optional[str]:
        """Return the current focus topic, or None."""
        return self._working.get('current_focus')

    def push_action(self, action: str, result_summary: Optional[str]=None) -> None:
        """Record an action in recent_actions (max ``_MAX_RECENT_ACTIONS``).

        Parameters
        ----------
        action : str
            The action name or description.
        result_summary : str or None
            Optional short description of the result.
        """
        entry = {'action': action, 'result_summary': result_summary, 'timestamp': _timestamp()}
        self._working['recent_actions'].append(entry)
        if len(self._working['recent_actions']) > _MAX_RECENT_ACTIONS:
            self._working['recent_actions'].pop(0)

    def get_recent_actions(self, n: int=10) -> List[Dict[str, Any]]:
        """Return the *n* most recent actions (newest first)."""
        actions = self._working.get('recent_actions', [])
        return list(reversed(actions[-n:]))

    def push_tool_call(self, tool_name: str, args: Dict[str, Any]) -> None:
        """Push a tool call onto the tool call stack."""
        self._working['tool_call_stack'].append({'tool': tool_name, 'args': args, 'timestamp': _timestamp()})

    def pop_tool_call(self) -> Optional[Dict[str, Any]]:
        """Pop the most recent tool call from the stack."""
        if self._working['tool_call_stack']:
            return self._working['tool_call_stack'].pop()
        return None

    def get_tool_call_stack(self) -> List[Dict[str, Any]]:
        """Return the current tool call stack (deepest first)."""
        return self._working.get('tool_call_stack', [])

    def set_goal(self, goal: str) -> None:
        """Set the current goal."""
        self._working['current_goal'] = goal

    def get_goal(self) -> Optional[str]:
        """Return the current goal."""
        return self._working.get('current_goal')

    def set_active_task(self, task_id: str) -> None:
        """Set the active task id in working memory."""
        self._working['active_task'] = task_id

    def get_active_task(self) -> Optional[str]:
        """Return the current active task id."""
        return self._working.get('active_task')

    def clear_working(self) -> None:
        """Reset all working memory fields."""
        self._working = {'active_task': None, 'current_focus': None, 'recent_actions': [], 'tool_call_stack': [], 'current_goal': None}
        self._log_event('memory.working.clear', {})

    def remember_episode(self, description: str, summary: str, outcome: str, tags: Optional[List[str]]=None) -> int:
        """Store a new episodic memory entry.

        Parameters
        ----------
        description : str
            Full description of the episode.
        summary : str
            Brief summary of what happened.
        outcome : str
            Outcome description (e.g. ``"success"``, ``"failed"``).
        tags : list of str or None
            Optional tags for categorisation.

        Returns
        -------
        int
            The auto-increment id of the new episode.
        """
        now = _timestamp()
        sess = _session_id()
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        conn = self._get_conn()
        cur = conn.execute('INSERT INTO episodic_memories\n               (session_id, description, summary, outcome, tags, created_at)\n               VALUES (?, ?, ?, ?, ?, ?)', (sess, description, summary, outcome, tags_json, now))
        conn.commit()
        ep_id = cur.lastrowid
        self._log_event('memory.episodic.store', {'episode_id': ep_id, 'session_id': sess, 'summary': summary, 'outcome': outcome})
        return ep_id

    def recall_episodes(self, query: Optional[str]=None, limit: int=10) -> List[Dict[str, Any]]:
        """Search episodic memories by text match on *query*.

        If *query* is None, returns the most recent episodes.
        """
        conn = self._get_conn()
        if query:
            like = f'%{query}%'
            cur = conn.execute('SELECT * FROM episodic_memories\n                   WHERE description LIKE ? OR summary LIKE ? OR outcome LIKE ?\n                   ORDER BY id DESC LIMIT ?', (like, like, like, limit))
        else:
            cur = conn.execute('SELECT * FROM episodic_memories ORDER BY id DESC LIMIT ?', (limit,))
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        for row in rows:
            conn.execute('UPDATE episodic_memories SET access_count = access_count + 1 WHERE id = ?', (row['id'],))
        conn.commit()
        return rows

    def recall_session(self, session_id: str, limit: int=10) -> List[Dict[str, Any]]:
        """Return episodes belonging to a specific *session_id*."""
        conn = self._get_conn()
        cur = conn.execute('SELECT * FROM episodic_memories WHERE session_id = ? ORDER BY id DESC LIMIT ?', (session_id, limit))
        return [_row_to_dict(r) for r in cur.fetchall()]

    def get_episode(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """Return a single episode by its id, or None."""
        conn = self._get_conn()
        cur = conn.execute('SELECT * FROM episodic_memories WHERE id = ?', (episode_id,))
        row = cur.fetchone()
        if row is None:
            return None
        conn.execute('UPDATE episodic_memories SET access_count = access_count + 1 WHERE id = ?', (episode_id,))
        conn.commit()
        return _row_to_dict(row)

    def learn_fact(self, fact: str, category: str='general', confidence: float=1.0, source: Optional[str]=None) -> int:
        """Store a semantic fact.

        Parameters
        ----------
        fact : str
            The fact text.
        category : str
            Category label (default ``"general"``).
        confidence : float
            Confidence value between 0.0 and 1.0 (default 1.0).
        source : str or None
            Source identifier (e.g. ``"user"``, ``"tool.my_tool"``).

        Returns
        -------
        int
            The auto-increment id of the new fact.
        """
        now = _timestamp()
        conn = self._get_conn()
        cur = conn.execute('INSERT INTO semantic_facts\n               (fact, category, confidence, source, created_at, last_accessed_at)\n               VALUES (?, ?, ?, ?, ?, ?)', (fact, category, min(max(confidence, 0.0), 1.0), source, now, now))
        conn.commit()
        fid = cur.lastrowid
        self._log_event('memory.semantic.store', {'fact_id': fid, 'fact': fact, 'category': category, 'confidence': confidence})
        return fid

    def recall_fact(self, query: Optional[str]=None, category: Optional[str]=None, min_confidence: float=0.0, limit: Optional[int]=None) -> List[Dict[str, Any]]:
        """Search semantic facts.

        Parameters
        ----------
        query : str or None
            Text to search in the fact text. None returns all.
        category : str or None
            Filter by category. None means no category filter.
        min_confidence : float
            Minimum confidence threshold (default 0.0).
        limit : int or None
            Maximum number of results. None means no limit.

        Returns
        -------
        list[dict]
            Matching fact rows.
        """
        conn = self._get_conn()
        conditions: List[str] = []
        params: List[Any] = []
        if query:
            conditions.append('fact LIKE ?')
            params.append(f'%{query}%')
        if category:
            conditions.append('category = ?')
            params.append(category)
        conditions.append('confidence >= ?')
        params.append(min_confidence)
        where = ' AND '.join(conditions) if conditions else '1=1'
        sql = f'SELECT * FROM semantic_facts WHERE {where} ORDER BY confidence DESC, access_count DESC'
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(limit)
        cur = conn.execute(sql, params)
        rows = [_row_to_dict(r) for r in cur.fetchall()]
        now = _timestamp()
        for row in rows:
            conn.execute('UPDATE semantic_facts SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?', (now, row['id']))
        conn.commit()
        return rows

    def reinforce_fact(self, fact_id: int) -> None:
        """Increase confidence and access_count of a fact."""
        conn = self._get_conn()
        now = _timestamp()
        conn.execute('UPDATE semantic_facts SET confidence = MIN(confidence + 0.1, 1.0), access_count = access_count + 1, last_accessed_at = ? WHERE id = ?', (now, fact_id))
        conn.commit()

    def forget_fact(self, fact_id: int) -> None:
        """Decrease confidence of a fact by 0.1 (minimum 0.0)."""
        conn = self._get_conn()
        conn.execute('UPDATE semantic_facts SET confidence = MAX(confidence - 0.1, 0.0) WHERE id = ?', (fact_id,))
        conn.commit()

    def get_fact_stats(self) -> Dict[str, Any]:
        """Return aggregate statistics about semantic facts."""
        conn = self._get_conn()
        cur = conn.execute('SELECT COUNT(*) AS total, AVG(confidence) AS avg_confidence, AVG(access_count) AS avg_access FROM semantic_facts')
        stats = dict(cur.fetchone() or {'total': 0, 'avg_confidence': 0.0, 'avg_access': 0.0})
        cur2 = conn.execute('SELECT category, COUNT(*) AS cnt FROM semantic_facts GROUP BY category ORDER BY cnt DESC')
        stats['by_category'] = {r['category']: r['cnt'] for r in cur2.fetchall()}
        return stats

    def learn_procedure(self, skill_name: str, trigger_conditions: str, steps: str, domain: Optional[str]=None) -> int:
        """Store a procedural memory (skill).

        Parameters
        ----------
        skill_name : str
            Name of the skill / procedure.
        trigger_conditions : str
            Description of when to apply this procedure.
        steps : str
            Steps (newline-separated or structured text).
        domain : str or None
            Domain label (e.g. ``"coding"``, ``"research"``).

        Returns
        -------
        int
            The auto-increment id of the new procedure.
        """
        conn = self._get_conn()
        now = _timestamp()
        cur = conn.execute('INSERT INTO procedural_memories\n               (skill_name, trigger_conditions, steps, domain, last_used_at)\n               VALUES (?, ?, ?, ?, ?)', (skill_name, trigger_conditions, steps, domain, now))
        conn.commit()
        pid = cur.lastrowid
        self._log_event('memory.procedural.store', {'proc_id': pid, 'skill_name': skill_name, 'domain': domain})
        return pid

    def recall_procedure(self, query: Optional[str]=None, domain: Optional[str]=None) -> List[Dict[str, Any]]:
        """Search procedural memories.

        Parameters
        ----------
        query : str or None
            Text search across skill_name, trigger_conditions, and steps.
        domain : str or None
            Filter by domain.

        Returns
        -------
        list[dict]
            Matching procedure rows, ordered by success rate descending.
        """
        conn = self._get_conn()
        conditions: List[str] = []
        params: List[Any] = []
        if query:
            like = f'%{query}%'
            conditions.append('(skill_name LIKE ? OR trigger_conditions LIKE ? OR steps LIKE ?)')
            params.extend([like, like, like])
        if domain:
            conditions.append('domain = ?')
            params.append(domain)
        where = ' AND '.join(conditions) if conditions else '1=1'
        sql = f'SELECT * FROM procedural_memories\n                  WHERE {where}\n                  ORDER BY\n                    CASE WHEN (success_count + fail_count) > 0\n                      THEN CAST(success_count AS REAL) / (success_count + fail_count)\n                      ELSE 0 END DESC,\n                    success_count DESC'
        cur = conn.execute(sql, params)
        return [_row_to_dict(r) for r in cur.fetchall()]

    def report_procedure_outcome(self, skill_name: str, success: bool, duration_s: float) -> None:
        """Record the outcome of using a procedure.

        Updates the matching row's success/fail count, average duration,
        and ``last_used_at`` timestamp.

        Parameters
        ----------
        skill_name : str
            Name of the skill / procedure.
        success : bool
            Whether the procedure succeeded.
        duration_s : float
            Duration in seconds.
        """
        conn = self._get_conn()
        now = _timestamp()
        if success:
            conn.execute('UPDATE procedural_memories\n                   SET success_count = success_count + 1,\n                       avg_duration_s = CASE\n                         WHEN (success_count + fail_count) > 0\n                         THEN (avg_duration_s * (success_count + fail_count) + ?) / (success_count + fail_count + 1)\n                         ELSE ? END,\n                       last_used_at = ?\n                   WHERE skill_name = ?', (duration_s, duration_s, now, skill_name))
        else:
            conn.execute('UPDATE procedural_memories\n                   SET fail_count = fail_count + 1,\n                       avg_duration_s = CASE\n                         WHEN (success_count + fail_count) > 0\n                         THEN (avg_duration_s * (success_count + fail_count) + ?) / (success_count + fail_count + 1)\n                         ELSE ? END,\n                       last_used_at = ?\n                   WHERE skill_name = ?', (duration_s, duration_s, now, skill_name))
        conn.commit()
        self._log_event('memory.procedural.outcome', {'skill_name': skill_name, 'success': success, 'duration_s': duration_s})

    def get_best_procedure(self, domain: str, query: str) -> Optional[Dict[str, Any]]:
        """Return the highest-success-rate procedure matching *domain* and *query*.

        Parameters
        ----------
        domain : str
            Domain to search within.
        query : str
            Text search across skill_name, trigger_conditions, and steps.

        Returns
        -------
        dict or None
            The best-matching procedure row, or None if no match.
        """
        results = self.recall_procedure(query=query, domain=domain)
        if not results:
            return None
        return results[0]

    def update_env(self, key: str, value: str, category: str='system', source: Optional[str]=None) -> None:
        """Upsert an environment fact.

        Parameters
        ----------
        key : str
            Unique key for the environment fact.
        value : str
            Value to store.
        category : str
            Category label (default ``"system"``).
        source : str or None
            Source identifier.
        """
        conn = self._get_conn()
        now = _timestamp()
        conn.execute('INSERT INTO environment_facts (key, value, category, last_verified_at, source)\n               VALUES (?, ?, ?, ?, ?)\n               ON CONFLICT(key) DO UPDATE SET\n                 value = excluded.value,\n                 category = COALESCE(excluded.category, category),\n                 last_verified_at = excluded.last_verified_at,\n                 source = COALESCE(excluded.source, source)', (key, value, category, now, source))
        conn.commit()
        self._log_event('memory.environment.update', {'key': key, 'value': value, 'category': category})

    def get_env_value(self, key: str) -> Optional[str]:
        """Return the value for an environment key, or None."""
        conn = self._get_conn()
        cur = conn.execute('SELECT value FROM environment_facts WHERE key = ?', (key,))
        row = cur.fetchone()
        return row['value'] if row else None

    def get_env_category(self, category: str) -> Dict[str, str]:
        """Return all environment facts in a given category as a key-value dict."""
        conn = self._get_conn()
        cur = conn.execute('SELECT key, value FROM environment_facts WHERE category = ?', (category,))
        return {row['key']: row['value'] for row in cur.fetchall()}

    def verify_all(self) -> None:
        """Mark all environment facts as verified right now."""
        now = _timestamp()
        conn = self._get_conn()
        conn.execute('UPDATE environment_facts SET last_verified_at = ?', (now,))
        conn.commit()

    def list_env_categories(self) -> List[str]:
        """Return all distinct environment fact categories."""
        conn = self._get_conn()
        cur = conn.execute('SELECT DISTINCT category FROM environment_facts ORDER BY category')
        return [r['category'] for r in cur.fetchall()]

    def delete_env(self, key: str) -> None:
        """Delete an environment fact by key."""
        conn = self._get_conn()
        conn.execute('DELETE FROM environment_facts WHERE key = ?', (key,))
        conn.commit()

    def _get_semantic_index(self):
        """Lazy-load and return the SemanticRetrieval singleton."""
        if self._semantic_index is None:
            try:
                from .semantic_retrieval import SemanticRetrieval
            except ImportError:
                import importlib
                SemanticRetrieval = importlib.import_module("semantic_retrieval").SemanticRetrieval
            self._semantic_index = SemanticRetrieval()
        return self._semantic_index

    def build_semantic_index(self) -> int:
        """Build TF-IDF index from all episodic + semantic memories.

        Tries to load from disk cache first. If cache is valid and DB
        count matches, uses cached index. Otherwise rebuilds from DB
        and saves cache.

        Returns the number of documents indexed.
        """
        sr = self._get_semantic_index()

        # Try cache first
        if sr.load_index():
            # Validate: check if DB count matches cached count
            conn = self._get_conn()
            db_count = 0
            for tbl in ('episodic_memories', 'semantic_facts', 'procedural_memories'):
                try:
                    cur = conn.execute(f'SELECT COUNT(*) FROM {tbl}')
                    db_count += cur.fetchone()[0]
                except Exception:
                    pass
            if db_count == sr._total_docs:
                return sr._total_docs  # cache hit

        # Cache miss — rebuild from DB
        conn = self._get_conn()

        # Collect episodic memories
        docs: list[dict] = []
        cur = conn.execute('SELECT id, description, summary, outcome, tags FROM episodic_memories')
        for row in cur.fetchall():
            text = ' '.join(filter(None, [row['description'], row['summary'], row['outcome']]))
            if text.strip():
                docs.append({
                    'memory_id': f'ep_{row["id"]}',
                    'text': text,
                    'category': 'episodic',
                })

        # Collect semantic facts
        cur = conn.execute('SELECT id, fact, category FROM semantic_facts')
        for row in cur.fetchall():
            text = row['fact'] or ''
            if text.strip():
                docs.append({
                    'memory_id': f'sf_{row["id"]}',
                    'text': text,
                    'category': 'semantic',
                })

        # Collect procedures
        cur = conn.execute('SELECT id, skill_name, trigger_conditions, steps FROM procedural_memories')
        for row in cur.fetchall():
            text = ' '.join(filter(None, [row['skill_name'], row['trigger_conditions'], row['steps']]))
            if text.strip():
                docs.append({
                    'memory_id': f'pm_{row["id"]}',
                    'text': text,
                    'category': 'procedural',
                })

        sr.index_memories(docs)
        # Persist cache for fast reload
        try:
            sr.save_index()
        except Exception:
            pass  # cache save failure is non-critical
        return len(docs)

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict]:
        """Search memories using TF-IDF cosine similarity.

        Builds the index on first call if not already built.
        Returns list of {memory_id, text, category, score}.
        """
        sr = self._get_semantic_index()
        # Rebuild index if empty (first call or DB changed)
        if sr._total_docs == 0:
            self.build_semantic_index()
        return sr.search(query, top_k=top_k)

    def search_all(self, query: str, limit: int=5) -> Dict[str, List[Dict[str, Any]]]:
        """Search across all persisted memory layers.

        Parameters
        ----------
        query : str
            Text to search across all layers.
        limit : int
            Max results per layer.

        Returns
        -------
        dict
            Keys: ``"episodic"``, ``"semantic"``, ``"procedural"``, ``"environment"``.
            Each value is a list of matching rows.
        """
        results: Dict[str, List[Dict[str, Any]]] = {}
        results['episodic'] = self.recall_episodes(query=query, limit=limit)
        results['semantic'] = self.recall_fact(query=query, limit=limit)
        results['procedural'] = self.recall_procedure(query=query)
        conn = self._get_conn()
        like = f'%{query}%'
        cur = conn.execute('SELECT * FROM environment_facts WHERE key LIKE ? OR value LIKE ? LIMIT ?', (like, like, limit))
        results['environment'] = [_row_to_dict(r) for r in cur.fetchall()]

        # TF-IDF semantic search (complements SQL LIKE queries)
        try:
            results['semantic_tfidf'] = self.semantic_search(query, top_k=limit)
        except Exception:
            results['semantic_tfidf'] = []

        return results

    def consolidate(self) -> Dict[str, Any]:
        """Run memory consolidation routines.

        - Merge similar episodic memories whose cumulative access_count
          exceeds the consolidation threshold.
        - Prune low-confidence semantic facts.

        Returns
        -------
        dict
            Summary of consolidation actions taken.
        """
        report: Dict[str, Any] = {'episodes_merged': 0, 'semantic_pruned': 0}
        conn = self._get_conn()
        cur = conn.execute('SELECT id, summary, outcome, tags, access_count\n               FROM episodic_memories\n               WHERE access_count >= ?\n               ORDER BY id ASC', (_EPISODE_CONSOLIDATION_THRESHOLD,))
        frequent = cur.fetchall()
        groups: Dict[str, List[sqlite3.Row]] = {}
        for row in frequent:
            prefix = (row['summary'] or '')[:60]
            groups.setdefault(prefix, []).append(row)
        merged_ids: List[int] = []
        for prefix, rows in groups.items():
            if len(rows) < 2:
                continue
            kept = rows[0]
            merged_sessions = [r['id'] for r in rows[1:]]
            merged_ids.extend(merged_sessions)
            new_summary = f"{kept['summary']} (consolidated)"
            old_tags = _maybe_json_load(kept['tags']) or []
            merged_tags_set = set(old_tags if isinstance(old_tags, list) else [])
            for r in rows[1:]:
                tags = _maybe_json_load(r['tags']) or []
                if isinstance(tags, list):
                    merged_tags_set.update(tags)
            new_tags = json.dumps(list(merged_tags_set), ensure_ascii=False)
            conn.execute('UPDATE episodic_memories SET summary = ?, tags = ? WHERE id = ?', (new_summary, new_tags, kept['id']))
        if merged_ids:
            placeholders = ','.join(('?' for _ in merged_ids))
            conn.execute(f'DELETE FROM episodic_memories WHERE id IN ({placeholders})', merged_ids)
            report['episodes_merged'] = len(merged_ids)
        cur = conn.execute('SELECT id FROM semantic_facts WHERE confidence < ?', (_SEMANTIC_PRUNE_CONFIDENCE,))
        low_conf = [r['id'] for r in cur.fetchall()]
        if low_conf:
            placeholders = ','.join(('?' for _ in low_conf))
            conn.execute(f'DELETE FROM semantic_facts WHERE id IN ({placeholders})', low_conf)
            report['semantic_pruned'] = len(low_conf)
        conn.commit()
        if report['episodes_merged'] > 0 or report['semantic_pruned'] > 0:
            self._log_event('memory.consolidate', report)
        return report

    def apply_decay(self, decay_rate: float=0.05) -> None:
        """Age all memories naturally.

        - Semantic facts: decrease confidence by *decay_rate* (min 0.0).
        - Episodic memories older than 30 days: halve access_count.
        - Procedural memories not used in 30 days: reduce success_count weight.
        """
        conn = self._get_conn()
        now = _timestamp()
        conn.execute('UPDATE semantic_facts SET confidence = MAX(confidence - ?, 0.0)', (decay_rate,))
        conn.execute('UPDATE episodic_memories\n               SET access_count = MAX(CAST(access_count AS REAL) / 2, 0)\n               WHERE CAST(julianday(?) - julianday(created_at) AS INTEGER) > 30', (now,))
        conn.execute('UPDATE procedural_memories\n               SET success_count = MAX(success_count - 1, 0)\n               WHERE last_used_at IS NOT NULL\n                 AND CAST(julianday(?) - julianday(last_used_at) AS INTEGER) > 30', (now,))
        conn.commit()
        self._log_event('memory.hygiene.decay', {'decay_rate': decay_rate})

    def deduplicate(self) -> dict:
        """Find and merge duplicate memories.

        Semantic: merge facts where text similarity exceeds threshold (exact match
        or one is substring of the other). Keep higher confidence, merge access_count.

        Episodic: merge episodes sharing the same description prefix (first 80 chars).

        Returns
        -------
        dict
            ``{semantic_merged: int, episodic_merged: int, removed: int}``
        """
        conn = self._get_conn()
        result: dict = {'semantic_merged': 0, 'episodic_merged': 0, 'removed': 0}
        cur = conn.execute('SELECT id, fact, confidence, access_count FROM semantic_facts ORDER BY id')
        facts = cur.fetchall()
        merged_ids: set[int] = set()
        for i in range(len(facts)):
            if facts[i]['id'] in merged_ids:
                continue
            for j in range(i + 1, len(facts)):
                if facts[j]['id'] in merged_ids:
                    continue
                a, b = (facts[i]['fact'], facts[j]['fact'])
                if a == b or (len(a) > 10 and a in b) or (len(b) > 10 and b in a):
                    if facts[i]['confidence'] >= facts[j]['confidence']:
                        keep, drop = (facts[i], facts[j])
                    else:
                        keep, drop = (facts[j], facts[i])
                    conn.execute('UPDATE semantic_facts SET access_count = ?, confidence = ? WHERE id = ?', (keep['access_count'] + drop['access_count'], max(keep['confidence'], drop['confidence']), keep['id']))
                    merged_ids.add(drop['id'])
                    result['semantic_merged'] += 1
        if merged_ids:
            placeholders = ','.join(('?' for _ in merged_ids))
            conn.execute(f'DELETE FROM semantic_facts WHERE id IN ({placeholders})', list(merged_ids))
            result['removed'] += len(merged_ids)
        cur = conn.execute('SELECT id, description FROM episodic_memories ORDER BY id')
        episodes = cur.fetchall()
        ep_merged_ids: set[int] = set()
        for i in range(len(episodes)):
            if episodes[i]['id'] in ep_merged_ids:
                continue
            matches = [episodes[i]]
            desc_prefix = (episodes[i]['description'] or '')[:80]
            for j in range(i + 1, len(episodes)):
                if episodes[j]['id'] in ep_merged_ids:
                    continue
                if (episodes[j]['description'] or '')[:80] == desc_prefix:
                    matches.append(episodes[j])
            if len(matches) > 1:
                kept = matches[0]
                for m in matches[1:]:
                    ep_merged_ids.add(m['id'])
                    result['episodic_merged'] += 1
        if ep_merged_ids:
            placeholders = ','.join(('?' for _ in ep_merged_ids))
            conn.execute(f'DELETE FROM episodic_memories WHERE id IN ({placeholders})', list(ep_merged_ids))
            result['removed'] += len(ep_merged_ids)
        conn.commit()
        self._log_event('memory.hygiene.dedup', result)
        return result

    def resolve_conflicts(self) -> list[dict]:
        """Find and resolve contradictory semantic facts.

        Looks for facts with the same category whose texts indicate opposition
        (e.g. one contains "not" and the other doesn't, or one negates the other).

        Resolution: keep the fact with higher confidence; if equal, keep the newer one.

        Returns
        -------
        list[dict]
            Each entry: ``{fact_a: str, fact_b: str, resolution: str, kept_id: int}``
        """
        conn = self._get_conn()
        resolved: list[dict] = []
        cur = conn.execute('SELECT id, fact, category, confidence, created_at FROM semantic_facts ORDER BY category, id')
        facts = cur.fetchall()
        by_cat: dict[str, list[sqlite3.Row]] = {}
        for row in facts:
            by_cat.setdefault(row['category'], []).append(row)

        def _is_negation(a: str, b: str) -> bool:
            """Check if two facts contradict each other."""
            a_lower, b_lower = (a.lower(), b.lower())
            negations = {'not ', 'no ', 'never ', 'cannot ', "isn't ", "aren't ", "don't ", "doesn't "}
            a_has_neg = any((n in a_lower for n in negations))
            b_has_neg = any((n in b_lower for n in negations))
            if a_has_neg != b_has_neg:
                common_words = set(a_lower.split()) & set(b_lower.split())
                if len(common_words) >= 3:
                    return True
            return False
        for cat, rows in by_cat.items():
            if len(rows) < 2:
                continue
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    a, b = (rows[i], rows[j])
                    if _is_negation(a['fact'], b['fact']):
                        if a['confidence'] > b['confidence']:
                            kept, dropped = (a, b)
                        elif b['confidence'] > a['confidence']:
                            kept, dropped = (b, a)
                        else:
                            kept, dropped = (a, b) if a['created_at'] >= b['created_at'] else (b, a)
                        resolved.append({'fact_a': a['fact'], 'fact_b': b['fact'], 'resolution': f"kept id={kept['id']} (confidence={kept['confidence']})", 'kept_id': kept['id']})
                        conn.execute('DELETE FROM semantic_facts WHERE id = ?', (dropped['id'],))
        if resolved:
            conn.commit()
            self._log_event('memory.hygiene.conflict', {'resolved': len(resolved)})
        return resolved

    def compress(self, age_days: int=14) -> None:
        """Compress old, rarely-accessed memories.

        - Episodic memories older than *age_days* with access_count < 3: summarize.
        - Semantic facts with confidence < 0.1 AND access_count < 2: archive (delete).
        """
        conn = self._get_conn()
        now = _timestamp()
        report: dict = {'episodic_compressed': 0, 'semantic_archived': 0}
        cur = conn.execute('SELECT id, summary, outcome, tags\n               FROM episodic_memories\n               WHERE CAST(julianday(?) - julianday(created_at) AS INTEGER) > ?\n                 AND access_count < 3', (now, age_days))
        old_eps = cur.fetchall()
        for row in old_eps:
            new_summary = f"{row['summary'] or ''} (compressed)"
            conn.execute('UPDATE episodic_memories SET summary = ? WHERE id = ?', (new_summary, row['id']))
            report['episodic_compressed'] += 1
        cur = conn.execute('SELECT id FROM semantic_facts WHERE confidence < 0.1 AND access_count < 2')
        low_ids = [r['id'] for r in cur.fetchall()]
        if low_ids:
            placeholders = ','.join(('?' for _ in low_ids))
            conn.execute(f'DELETE FROM semantic_facts WHERE id IN ({placeholders})', low_ids)
            report['semantic_archived'] = len(low_ids)
        conn.commit()
        if report['episodic_compressed'] > 0 or report['semantic_archived'] > 0:
            self._log_event('memory.hygiene.compress', report)

    def prune(self) -> dict:
        """Aggressively remove useless memories.

        - Semantic: confidence < 0.05 AND access_count < 2.
        - Episodic: access_count = 0 AND age > 60 days.
        - Procedural: fail_count > 10 AND success_count = 0.

        Returns
        -------
        dict
            ``{semantic_pruned: int, episodic_pruned: int, procedural_pruned: int}``
        """
        conn = self._get_conn()
        now = _timestamp()
        report: dict = {'semantic_pruned': 0, 'episodic_pruned': 0, 'procedural_pruned': 0}
        cur = conn.execute('SELECT id FROM semantic_facts WHERE confidence < 0.05 AND access_count < 2')
        sem_ids = [r['id'] for r in cur.fetchall()]
        if sem_ids:
            placeholders = ','.join(('?' for _ in sem_ids))
            conn.execute(f'DELETE FROM semantic_facts WHERE id IN ({placeholders})', sem_ids)
            report['semantic_pruned'] = len(sem_ids)
        cur = conn.execute('SELECT id FROM episodic_memories\n               WHERE access_count = 0\n                 AND CAST(julianday(?) - julianday(created_at) AS INTEGER) > 60', (now,))
        ep_ids = [r['id'] for r in cur.fetchall()]
        if ep_ids:
            placeholders = ','.join(('?' for _ in ep_ids))
            conn.execute(f'DELETE FROM episodic_memories WHERE id IN ({placeholders})', ep_ids)
            report['episodic_pruned'] = len(ep_ids)
        cur = conn.execute('SELECT id FROM procedural_memories WHERE fail_count > 10 AND success_count = 0')
        proc_ids = [r['id'] for r in cur.fetchall()]
        if proc_ids:
            placeholders = ','.join(('?' for _ in proc_ids))
            conn.execute(f'DELETE FROM procedural_memories WHERE id IN ({placeholders})', proc_ids)
            report['procedural_pruned'] = len(proc_ids)
        conn.commit()
        if any(report.values()):
            self._log_event('memory.hygiene.prune', report)
        return report

    def run_hygiene(self) -> dict:
        """Run the full memory hygiene cycle.

        Order: deduplicate → resolve_conflicts → apply_decay → compress → prune

        Returns
        -------
        dict
            Full report aggregating results from each phase.
        """
        report: dict = {}
        dedup_result = self.deduplicate()
        report['deduplicate'] = dedup_result
        conflicts = self.resolve_conflicts()
        report['conflicts_resolved'] = len(conflicts)
        report['conflict_details'] = conflicts
        self.apply_decay()
        report['decay_applied'] = True
        self.compress()
        report['compress_applied'] = True
        prune_result = self.prune()
        report['prune'] = prune_result
        report['total_removed'] = dedup_result.get('removed', 0) + prune_result.get('semantic_pruned', 0) + prune_result.get('episodic_pruned', 0) + prune_result.get('procedural_pruned', 0)
        self._log_event('memory.hygiene.full', report)
        return report

    def get_health(self) -> dict:
        """Return a health assessment of the memory system.

        Returns
        -------
        dict
            Keys: total_memories, avg_confidence, stale_count,
                  duplicate_pairs, conflicts, hygiene_score
        """
        conn = self._get_conn()
        now = _timestamp()

        def _cnt(table: str) -> int:
            c = conn.execute(f'SELECT COUNT(*) AS n FROM {table}')
            return c.fetchone()['n']
        total = {'episodic': _cnt('episodic_memories'), 'semantic': _cnt('semantic_facts'), 'procedural': _cnt('procedural_memories'), 'environment': _cnt('environment_facts')}
        cur = conn.execute('SELECT COALESCE(AVG(confidence), 0.0) AS ac FROM semantic_facts')
        avg_confidence = cur.fetchone()['ac']
        cur = conn.execute('SELECT COUNT(*) AS n FROM semantic_facts\n               WHERE last_accessed_at IS NOT NULL\n                 AND CAST(julianday(?) - julianday(last_accessed_at) AS INTEGER) > 30', (now,))
        stale_semantic = cur.fetchone()['n']
        cur = conn.execute('SELECT COUNT(*) AS n FROM episodic_memories\n               WHERE CAST(julianday(?) - julianday(created_at) AS INTEGER) > 30', (now,))
        stale_episodic = cur.fetchone()['n']
        cur = conn.execute('SELECT COUNT(*) AS n FROM procedural_memories\n               WHERE last_used_at IS NOT NULL\n                 AND CAST(julianday(?) - julianday(last_used_at) AS INTEGER) > 30', (now,))
        stale_procedural = cur.fetchone()['n']
        stale_count = stale_semantic + stale_episodic + stale_procedural
        cur = conn.execute('SELECT fact FROM semantic_facts ORDER BY id')
        facts = [r['fact'] for r in cur.fetchall()]
        duplicate_pairs = 0
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                a, b = (facts[i], facts[j])
                if a == b or (len(a) > 10 and a in b) or (len(b) > 10 and b in a):
                    duplicate_pairs += 1
        cur = conn.execute('SELECT category, fact FROM semantic_facts ORDER BY category')
        rows = cur.fetchall()
        by_cat: dict[str, list[str]] = {}
        for r in rows:
            by_cat.setdefault(r['category'], []).append(r['fact'])
        conflicts = 0
        negations_set = {'not ', 'no ', 'never ', 'cannot ', "isn't ", "aren't "}
        for cat, texts in by_cat.items():
            for i in range(len(texts)):
                for j in range(i + 1, len(texts)):
                    a_low, b_low = (texts[i].lower(), texts[j].lower())
                    a_has = any((n in a_low for n in negations_set))
                    b_has = any((n in b_low for n in negations_set))
                    if a_has != b_has:
                        common = set(a_low.split()) & set(b_low.split())
                        if len(common) >= 3:
                            conflicts += 1
        total_mems = sum(total.values())
        stale_penalty = min(1.0, stale_count / max(1, total_mems)) * 0.4
        dup_penalty = min(1.0, duplicate_pairs / max(1, total_mems)) * 0.3
        conflict_penalty = min(1.0, conflicts / max(1, total_mems)) * 0.3
        hygiene_score = max(0.0, 1.0 - stale_penalty - dup_penalty - conflict_penalty)
        return {'total_memories': total, 'avg_confidence': round(avg_confidence, 4), 'stale_count': stale_count, 'duplicate_pairs': duplicate_pairs, 'conflicts': conflicts, 'hygiene_score': round(hygiene_score, 4)}

    def get_stats(self) -> Dict[str, Any]:
        """Return memory usage statistics across all layers.

        Returns
        -------
        dict
            Keys: ``"working"``, ``"episodic"``, ``"semantic"``, ``"procedural"``,
            ``"environment"``, ``"total_db_size_bytes"``.
        """
        conn = self._get_conn()
        working_stats = {'has_focus': self._working.get('current_focus') is not None, 'recent_actions_count': len(self._working.get('recent_actions', [])), 'tool_call_stack_depth': len(self._working.get('tool_call_stack', [])), 'has_goal': self._working.get('current_goal') is not None, 'has_active_task': self._working.get('active_task') is not None}

        def _count(table: str) -> int:
            cur = conn.execute(f'SELECT COUNT(*) AS cnt FROM {table}')
            return cur.fetchone()['cnt']
        episodic_count = _count('episodic_memories')
        semantic_count = _count('semantic_facts')
        procedural_count = _count('procedural_memories')
        environment_count = _count('environment_facts')
        db_path = self._schema_mgr.db_path(_DB_NAME)
        db_size = 0
        try:
            db_size = db_path.stat().st_size
        except OSError:
            pass
        return {'working': working_stats, 'episodic': {'count': episodic_count}, 'semantic': {'count': semantic_count, **self.get_fact_stats()}, 'procedural': {'count': procedural_count, 'total_uses': sum((r['total'] for r in conn.execute('SELECT SUM(success_count + fail_count) AS total FROM procedural_memories').fetchall()))}, 'environment': {'count': environment_count}, 'total_db_size_bytes': db_size}

    def close(self) -> None:
        """Release database connections."""
        if hasattr(self, '_schema_mgr'):
            try:
                self._schema_mgr.close(_DB_NAME)
            except Exception as exc:
                logger.debug('memory_manager: close: %s', exc)
_default_memory_manager: Optional[MemoryManager] = None
_default_memory_manager_lock = threading.Lock()

def get_memory_manager(data_dir: Optional[Path]=None) -> MemoryManager:
    """Return the module-level ``MemoryManager`` singleton."""
    global _default_memory_manager
    with _default_memory_manager_lock:
        if _default_memory_manager is None:
            _default_memory_manager = MemoryManager(data_dir)
        elif data_dir is not None:
            pass
        return _default_memory_manager

def get_memory(data_dir: Optional[Path]=None) -> MemoryManager:
    """Alias for ``get_memory_manager()``."""
    return get_memory_manager(data_dir)