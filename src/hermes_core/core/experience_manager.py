"""experience_manager.py — Experience accumulation system for Hermes Core.

Tracks successful patterns, failure patterns, and tool usage statistics
in SQLite databases, enabling data-driven decision-making about which
strategies and tools to prefer.

Standard library only: sqlite3, uuid, datetime, json, pathlib, threading.
"""

from __future__ import annotations

import logging
logger = logging.getLogger(__name__)
import json
import threading
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional
import os
try:
    from .db_schema import get_manager as _get_schema_manager
    from .event_logger import get_logger
    from .exceptions import HermesCoreError
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from db_schema import get_manager as _get_schema_manager
    from event_logger import get_logger
    from exceptions import HermesCoreError
_REFLECTION_ENGINE_MODULE = 'reflection_engine'
_EXPERIENCE_DB = 'experience'
_SUCCESS_TABLE = 'successful_patterns'
_FAILURE_TABLE = 'failure_patterns'
_TOOL_TABLE = 'tool_usage_stats'
_SESSION_ID = os.environ.get('HERMES_SESSION_ID', 'unknown')
_instance: Optional['ExperienceManager'] = None
_instance_lock = threading.Lock()

class ExperienceManager:
    """Singleton engine for accumulating and querying experiential knowledge.

    Records successes, failures, and tool-usage statistics across task
    executions.  Provides query methods to retrieve best tools, strategies,
    and known failure patterns for data-driven decision-making.
    """

    def __init__(self, schema_manager: Any=None) -> None:
        """Initialise the experience manager.

        Parameters
        ----------
        schema_manager : SchemaManager or None
            External SchemaManager instance (for testing).  If ``None``,
            the module-level singleton is used.
        """
        if getattr(self, '_initialized', False):
            return
        self._schema_mgr = schema_manager if schema_manager is not None else _get_schema_manager()
        self._schema_mgr.initialize(_EXPERIENCE_DB)
        self._lock = threading.Lock()
        self._logger = get_logger()
        self._migrate_schema()
        self._initialized = True

    def _migrate_schema(self) -> None:
        """Add new confidence/decay columns to existing tables (idempotent).

        Uses ALTER TABLE ADD COLUMN with IF NOT EXISTS checks since SQLite
        does not support IF NOT EXISTS for ALTER TABLE directly.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        columns = [('confidence', 'REAL DEFAULT 0.5'), ('sample_size', 'INTEGER DEFAULT 1'), ('verification_count', 'INTEGER DEFAULT 1'), ('decay_rate', 'REAL DEFAULT 0.01'), ('last_verified_at', 'TEXT'), ('last_success_at', 'TEXT')]
        for col_name, col_def in columns:
            try:
                conn.execute(f'ALTER TABLE {_SUCCESS_TABLE} ADD COLUMN {col_name} {col_def}')
            except Exception as exc:
                logger.debug('experience_manager: _migrate_schema: %s', exc)
        conn.commit()

    def record_success(self, pattern_name: str, action_sequence: list[str], duration_s: float, domain: Optional[str]=None, tags: Optional[list[str]]=None) -> None:
        """Record a successful pattern, upserting into successful_patterns.

        If a pattern with the same *pattern_name* already exists, its
        ``success_count`` is incremented, ``avg_duration`` is updated
        as a running average, and ``last_used_at`` is refreshed.

        Parameters
        ----------
        pattern_name : str
            Identifier for the pattern (e.g. ``"web_scrape_pagination"``).
        action_sequence : list[str]
            Ordered list of action/tool names that comprised the pattern.
        duration_s : float
            How long the pattern took to execute (seconds).
        domain : str or None
            Optional domain category (e.g. ``"web"``, ``"file"``, ``"api"``).
        tags : list[str] or None
            Optional tags for categorisation and filtering.
        """
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        actions_json = json.dumps(action_sequence, ensure_ascii=False)
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        with self._lock:
            existing = conn.execute(f'SELECT success_count, avg_duration_seconds, sample_size, last_verified_at, last_success_at, last_used_at, decay_rate, fail_count FROM {_SUCCESS_TABLE} WHERE pattern_name = ?', (pattern_name,)).fetchone()
            if existing:
                old_count = existing['success_count']
                old_avg = existing['avg_duration_seconds'] or 0.0
                new_count = old_count + 1
                new_avg = (old_avg * old_count + duration_s) / new_count
                old_sample = existing['sample_size'] if existing['sample_size'] else old_count
                new_sample = old_sample + 1
                total_attempts = max(1, old_sample + 1)
                raw_rate = (old_count + 1) / total_attempts
                days_since = 0.0
                last_verify = existing['last_verified_at'] or existing['last_success_at'] or existing['last_used_at'] or now
                try:
                    lv = datetime.fromisoformat(last_verify)
                    days_since = (datetime.now(timezone.utc) - lv).total_seconds() / 86400.0
                except Exception:
                    days_since = 0.0
                decay_r = existing['decay_rate'] if existing['decay_rate'] else 0.01
                confidence_val = min(1.0, raw_rate * (1.0 - decay_r * days_since))
                if new_sample < 3 and confidence_val > 0.3:
                    confidence_val = 0.3
                conn.execute(f'\n                    UPDATE {_SUCCESS_TABLE}\n                    SET success_count = ?,\n                        avg_duration_seconds = ?,\n                        last_used_at = ?,\n                        last_success_at = ?,\n                        action_sequence = ?,\n                        domain = COALESCE(?, domain),\n                        tags = ?,\n                        sample_size = ?,\n                        confidence = ?\n                    WHERE pattern_name = ?\n                    ', (new_count, round(new_avg, 4), now, now, actions_json, domain, tags_json, new_sample, round(confidence_val, 4), pattern_name))
            else:
                conn.execute(f'\n                    INSERT INTO {_SUCCESS_TABLE}\n                        (pattern_name, action_sequence, success_count, fail_count,\n                         avg_duration_seconds, last_used_at, domain, tags,\n                         confidence, sample_size, verification_count,\n                         decay_rate, last_verified_at, last_success_at)\n                    VALUES (?, ?, 1, 0, ?, ?, ?, ?,\n                            0.5, 1, 1, 0.01, ?, ?)\n                    ', (pattern_name, actions_json, round(duration_s, 4), now, domain or '', tags_json, now, now))
            conn.commit()
        self._logger.log('experience.success_recorded', {'pattern_name': pattern_name, 'duration_s': duration_s, 'domain': domain})

    def record_failure(self, domain: str, error_type: str, error_message: str, resolution: Optional[str]=None) -> None:
        """Record a failure pattern, upserting into failure_patterns.

        If a matching record (same *domain*, *error_type*, *error_message*)
        exists, its ``count`` is incremented and ``last_seen_at`` updated.

        Parameters
        ----------
        domain : str
            Domain category of the failure (e.g. ``"web"``, ``"file"``).
        error_type : str
            Classification of the error (e.g. ``"timeout"``, ``"auth_error"``).
        error_message : str
            The actual error text.
        resolution : str or None
            Optional description of how the issue was resolved.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        with self._lock:
            existing = conn.execute(f'SELECT id, count, first_seen_at FROM {_FAILURE_TABLE} WHERE domain = ? AND error_type = ? AND error_message = ?', (domain, error_type, error_message)).fetchone()
            if existing:
                conn.execute(f'\n                    UPDATE {_FAILURE_TABLE}\n                    SET count = count + 1,\n                        last_seen_at = ?,\n                        resolution = COALESCE(?, resolution)\n                    WHERE id = ?\n                    ', (now, resolution, existing['id']))
            else:
                conn.execute(f'\n                    INSERT INTO {_FAILURE_TABLE}\n                        (domain, error_type, error_message, count,\n                         first_seen_at, last_seen_at, resolution)\n                    VALUES (?, ?, ?, 1, ?, ?, ?)\n                    ', (domain, error_type, error_message, now, now, resolution or ''))
            conn.commit()
        self._logger.log('experience.failure_recorded', {'domain': domain, 'error_type': error_type, 'error_message_length': len(error_message), 'resolved': bool(resolution)})

    def record_tool_usage(self, tool_name: str, success: bool, cost: float=0.0, duration_s: float=0.0) -> None:
        """Record a tool usage event, upserting into tool_usage_stats.

        Parameters
        ----------
        tool_name : str
            Name of the tool (e.g. ``"web_search"``, ``"file_read"``).
        success : bool
            Whether the tool call was successful.
        cost : float
            Monetary or resource cost of the call (default 0).
        duration_s : float
            Duration of the call in seconds (default 0).
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        with self._lock:
            existing = conn.execute(f'SELECT * FROM {_TOOL_TABLE} WHERE tool_name = ?', (tool_name,)).fetchone()
            if existing:
                old_count = existing['call_count']
                old_success = existing['success_count']
                old_fail = existing['fail_count']
                old_avg_cost = existing['avg_cost'] or 0.0
                old_avg_dur = existing['avg_duration_seconds'] or 0.0
                new_count = old_count + 1
                if success:
                    new_success = old_success + 1
                    new_fail = old_fail
                else:
                    new_success = old_success
                    new_fail = old_fail + 1
                new_avg_cost = (old_avg_cost * old_count + cost) / new_count
                new_avg_dur = (old_avg_dur * old_count + duration_s) / new_count
                conn.execute(f'\n                    UPDATE {_TOOL_TABLE}\n                    SET call_count = ?,\n                        success_count = ?,\n                        fail_count = ?,\n                        avg_cost = ?,\n                        avg_duration_seconds = ?,\n                        last_used_at = ?\n                    WHERE tool_name = ?\n                    ', (new_count, new_success, new_fail, round(new_avg_cost, 6), round(new_avg_dur, 4), now, tool_name))
            else:
                conn.execute(f'\n                    INSERT INTO {_TOOL_TABLE}\n                        (tool_name, call_count, success_count, fail_count,\n                         avg_cost, avg_duration_seconds, last_used_at)\n                    VALUES (?, 1, ?, ?, ?, ?, ?)\n                    ', (tool_name, 1 if success else 0, 0 if success else 1, round(cost, 6), round(duration_s, 4), now))
            conn.commit()
        self._logger.log('experience.tool_usage_recorded', {'tool_name': tool_name, 'success': success, 'cost': cost, 'duration_s': duration_s})

    def calculate_confidence(self, pattern_name: Optional[str]=None) -> float:
        """Calculate confidence score for one or all patterns.

        Formula::
            confidence = min(1.0, (success_count / max(1, total_attempts))
                           * (1 - decay_rate * days_since_last_verify))

        If ``sample_size < 3``, confidence is capped at 0.3.
        If never re-verified in 7+ days, confidence is reduced by 50%.

        Parameters
        ----------
        pattern_name : str or None
            Specific pattern to calculate.  If ``None``, returns the average
            confidence across all patterns.

        Returns
        -------
        float
            Confidence value between 0.0 and 1.0.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        now_ts = datetime.now(timezone.utc)
        if pattern_name:
            rows = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE} WHERE pattern_name = ?', (pattern_name,)).fetchall()
        else:
            rows = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE}').fetchall()
        if not rows:
            return 0.0
        total_conf = 0.0
        for row in rows:
            d = dict(row)
            sc = d.get('success_count', 0) or 0
            fc = d.get('fail_count', 0) or 0
            total_attempts = max(1, sc + fc)
            raw_rate = sc / total_attempts
            last_verify = d.get('last_verified_at') or d.get('last_success_at') or d.get('last_used_at')
            days_since = 0.0
            if last_verify:
                try:
                    lv = datetime.fromisoformat(last_verify)
                    days_since = (now_ts - lv).total_seconds() / 86400.0
                    if days_since < 0:
                        days_since = 0.0
                except Exception:
                    days_since = 0.0
            decay_r = d.get('decay_rate', 0.01) or 0.01
            conf = min(1.0, raw_rate * (1.0 - decay_r * days_since))
            sample = d.get('sample_size', 1) or 1
            if sample < 3:
                conf = min(conf, 0.3)
            if days_since >= 7.0:
                conf *= 0.5
            total_conf += conf
        avg_conf = total_conf / len(rows)
        return round(avg_conf, 4)

    def get_high_confidence_strategies(self, domain: Optional[str]=None, min_confidence: float=0.5) -> list[dict[str, Any]]:
        """Return strategies with confidence >= *min_confidence*.

        Parameters
        ----------
        domain : str or None
            Optional domain filter.
        min_confidence : float
            Minimum confidence threshold (default 0.5).

        Returns
        -------
        list[dict]
            Each dict contains all columns plus computed ``confidence``.
            Ordered by confidence descending.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        if domain:
            rows = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE} WHERE domain = ? AND confidence >= ? ORDER BY confidence DESC, success_count DESC', (domain, min_confidence)).fetchall()
        else:
            rows = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE} WHERE confidence >= ? ORDER BY confidence DESC, success_count DESC', (min_confidence,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def verify_pattern(self, pattern_name: str) -> dict[str, Any]:
        """Re-verify a pattern by checking its current state.

        Increments ``verification_count``, updates ``last_verified_at``,
        and recalculates confidence.  If the pattern has a high fail_count
        relative to success_count, confidence is reduced.

        Parameters
        ----------
        pattern_name : str
            Name of the pattern to verify.

        Returns
        -------
        dict
            Result dict with keys: ``pattern_name``, ``verified`` (bool),
            ``confidence_before``, ``confidence_after``, ``verification_count``.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            row = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE} WHERE pattern_name = ?', (pattern_name,)).fetchone()
            if not row:
                return {'pattern_name': pattern_name, 'verified': False, 'error': 'Pattern not found'}
            d = dict(row)
            old_conf = d.get('confidence', 0.5) or 0.5
            sc = d.get('success_count', 0) or 0
            fc = d.get('fail_count', 0) or 0
            sample = d.get('sample_size', 1) or 1
            decay_r = d.get('decay_rate', 0.01) or 0.01
            total_attempts = max(1, sc + fc)
            raw_rate = sc / total_attempts
            last_verify = d.get('last_verified_at') or d.get('last_success_at') or d.get('last_used_at')
            days_since = 0.0
            if last_verify:
                try:
                    lv = datetime.fromisoformat(last_verify)
                    days_since = (datetime.now(timezone.utc) - lv).total_seconds() / 86400.0
                    if days_since < 0:
                        days_since = 0.0
                except Exception:
                    days_since = 0.0
            new_conf = min(1.0, raw_rate * (1.0 - decay_r * days_since))
            if sample < 3:
                new_conf = min(new_conf, 0.3)
            if days_since >= 7.0:
                new_conf *= 0.5
            if total_attempts >= 5 and fc > sc * 0.5:
                new_conf *= 0.7
            new_verifications = (d.get('verification_count', 0) or 0) + 1
            conn.execute(f'UPDATE {_SUCCESS_TABLE} SET verification_count = ?, last_verified_at = ?, confidence = ? WHERE pattern_name = ?', (new_verifications, now, round(new_conf, 4), pattern_name))
            conn.commit()
        return {'pattern_name': pattern_name, 'verified': True, 'confidence_before': round(old_conf, 4), 'confidence_after': round(new_conf, 4), 'verification_count': new_verifications}

    def decay_all(self, decay_rate: float=0.01) -> int:
        """Apply time decay to all patterns, reducing confidence over time.

        Called periodically by memory hygiene.  Recalculates every pattern's
        confidence based on elapsed time since last verification/success.

        Parameters
        ----------
        decay_rate : float
            Decay rate per day (default 0.01 = 1% per day).

        Returns
        -------
        int
            Number of patterns updated.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        now_ts = datetime.now(timezone.utc)
        with self._lock:
            conn.execute(f'UPDATE {_SUCCESS_TABLE} SET decay_rate = ? WHERE decay_rate IS NULL OR decay_rate = 0.0', (decay_rate,))
            rows = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE}').fetchall()
            updated = 0
            for row in rows:
                d = dict(row)
                pattern = d['pattern_name']
                sc = d.get('success_count', 0) or 0
                fc = d.get('fail_count', 0) or 0
                total_attempts = max(1, sc + fc)
                raw_rate = sc / total_attempts
                last_verify = d.get('last_verified_at') or d.get('last_success_at') or d.get('last_used_at')
                days_since = 0.0
                if last_verify:
                    try:
                        lv = datetime.fromisoformat(last_verify)
                        days_since = (now_ts - lv).total_seconds() / 86400.0
                        if days_since < 0:
                            days_since = 0.0
                    except Exception:
                        days_since = 0.0
                dec_r = d.get('decay_rate', decay_rate) or decay_rate
                conf = min(1.0, raw_rate * (1.0 - dec_r * days_since))
                sample = d.get('sample_size', 1) or 1
                if sample < 3:
                    conf = min(conf, 0.3)
                if days_since >= 7.0:
                    conf *= 0.5
                conn.execute(f'UPDATE {_SUCCESS_TABLE} SET confidence = ? WHERE pattern_name = ?', (round(conf, 4), pattern))
                updated += 1
            conn.commit()
        return updated

    def get_experience_health(self) -> dict[str, Any]:
        """Return cognitive health indicators for the experience database.

        Returns
        -------
        dict
            Keys:
            - ``total_patterns`` (int)
            - ``avg_confidence`` (float)
            - ``avg_sample_size`` (float)
            - ``stale_patterns`` (int) — patterns not re-verified in >7 days
            - ``low_confidence_count`` (int) — patterns with confidence < 0.3
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        now_ts = datetime.now(timezone.utc)
        rows = conn.execute(f'SELECT * FROM {_SUCCESS_TABLE}').fetchall()
        total = len(rows)
        if total == 0:
            return {'total_patterns': 0, 'avg_confidence': 0.0, 'avg_sample_size': 0.0, 'stale_patterns': 0, 'low_confidence_count': 0}
        total_conf = 0.0
        total_sample = 0
        stale = 0
        low_conf = 0
        for row in rows:
            d = dict(row)
            conf = d.get('confidence', 0.5) or 0.5
            sample = d.get('sample_size', 1) or 1
            total_conf += conf
            total_sample += sample
            if conf < 0.3:
                low_conf += 1
            last_verify = d.get('last_verified_at') or d.get('last_success_at') or d.get('last_used_at')
            if last_verify:
                try:
                    lv = datetime.fromisoformat(last_verify)
                    days_since = (now_ts - lv).total_seconds() / 86400.0
                    if days_since > 7.0:
                        stale += 1
                except Exception as exc:
                    logger.debug('experience_manager: get_experience_health: %s', exc)
        return {'total_patterns': total, 'avg_confidence': round(total_conf / total, 4), 'avg_sample_size': round(total_sample / total, 2), 'stale_patterns': stale, 'low_confidence_count': low_conf}

    def prune_low_confidence(self, threshold: float=0.1) -> int:
        """Remove patterns with confidence < *threshold* AND sample_size > 10.

        Small-sample patterns (sample_size <= 10) are kept — they may still
        be learning.  Called by memory hygiene.

        Parameters
        ----------
        threshold : float
            Confidence threshold below which patterns are pruned
            (default 0.1).

        Returns
        -------
        int
            Number of patterns pruned.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        with self._lock:
            result = conn.execute(f'DELETE FROM {_SUCCESS_TABLE} WHERE confidence < ? AND sample_size > 10', (threshold,))
            conn.commit()
            return result.rowcount

    def get_best_tool_for(self, capability: str) -> Optional[str]:
        """Find the tool with the highest confidence relevant to *capability*.

        Performs a fuzzy lookup: first tries an exact *pattern_name* match
        on successful_patterns (with confidence >= 0.3), then falls back to
        searching tool_usage_stats for tools whose name contains the capability
        string.

        High-confidence tools (confidence >= 0.5) are preferred.  If none exist,
        returns the least-bad option and logs a warning.

        Parameters
        ----------
        capability : str
            Description of the needed capability (e.g. ``"web_search"``,
            ``"file_read"``).

        Returns
        -------
        str or None
            Name of the best tool, or ``None`` if no candidate exists.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)

        def _best_with_conf(where_clause: str, params: tuple) -> Optional[str]:
            row = conn.execute(f'SELECT pattern_name, confidence, success_count FROM {_SUCCESS_TABLE} WHERE {where_clause} AND confidence >= 0.5 AND success_count > 0 ORDER BY confidence DESC, success_count DESC LIMIT 1', params).fetchone()
            if row:
                return row['pattern_name']
            row = conn.execute(f'SELECT pattern_name, confidence, success_count FROM {_SUCCESS_TABLE} WHERE {where_clause} AND confidence >= 0.3 AND success_count > 0 ORDER BY confidence DESC, success_count DESC LIMIT 1', params).fetchone()
            if row:
                self._logger.log('experience.low_confidence_fallback', {'capability': capability, 'tool': row['pattern_name'], 'confidence': row['confidence']})
                return row['pattern_name']
            return None
        result = _best_with_conf('pattern_name = ?', (capability,))
        if result:
            return result
        result = _best_with_conf('pattern_name LIKE ?', (f'%{capability}%',))
        if result:
            return result
        rows = conn.execute(f'SELECT tool_name, success_count, fail_count, call_count FROM {_TOOL_TABLE} WHERE tool_name LIKE ? AND call_count > 0 ORDER BY (CAST(success_count AS REAL) / call_count) DESC LIMIT 1', (f'%{capability}%',)).fetchall()
        if rows:
            return rows[0]['tool_name']
        row = conn.execute(f'SELECT tool_name, call_count, success_count FROM {_TOOL_TABLE} WHERE call_count > 0 ORDER BY (CAST(success_count AS REAL) / call_count) DESC LIMIT 1').fetchone()
        if row:
            self._logger.log('experience.no_confidence_match', {'capability': capability, 'fallback_tool': row['tool_name']})
            return row['tool_name']
        return None

    def get_strategies(self, domain: Optional[str]=None, min_success_rate: float=0.5) -> list[dict[str, Any]]:
        """Return successful patterns that meet a minimum success rate.

        Parameters
        ----------
        domain : str or None
            If set, filter patterns to this domain.
        min_success_rate : float
            Minimum ``success_count / (success_count + fail_count)`` ratio
            (default 0.5).

        Returns
        -------
        list[dict]
            Each dict contains all columns from ``successful_patterns`` plus a
            computed ``success_rate`` key.  Ordered by success_rate descending.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        if domain:
            rows = conn.execute(f'\n                SELECT *, \n                    CASE WHEN (success_count + fail_count) > 0\n                        THEN CAST(success_count AS REAL) / (success_count + fail_count)\n                        ELSE 1.0\n                    END AS success_rate\n                FROM {_SUCCESS_TABLE}\n                WHERE domain = ? \n                  AND (CAST(success_count AS REAL) / NULLIF(success_count + fail_count, 0)) >= ?\n                ORDER BY success_rate DESC, success_count DESC\n                ', (domain, min_success_rate)).fetchall()
        else:
            rows = conn.execute(f'\n                SELECT *,\n                    CASE WHEN (success_count + fail_count) > 0\n                        THEN CAST(success_count AS REAL) / (success_count + fail_count)\n                        ELSE 1.0\n                    END AS success_rate\n                FROM {_SUCCESS_TABLE}\n                WHERE (CAST(success_count AS REAL) / NULLIF(success_count + fail_count, 0)) >= ?\n                ORDER BY success_rate DESC, success_count DESC\n                ', (min_success_rate,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_known_failures(self, domain: Optional[str]=None) -> list[dict[str, Any]]:
        """Return known failure patterns, optionally filtered by domain.

        Parameters
        ----------
        domain : str or None
            If set, only return failures in this domain.

        Returns
        -------
        list[dict]
            Each dict contains all columns from ``failure_patterns``.
            Ordered by count descending.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        if domain:
            rows = conn.execute(f'SELECT * FROM {_FAILURE_TABLE} WHERE domain = ? ORDER BY count DESC, last_seen_at DESC', (domain,)).fetchall()
        else:
            rows = conn.execute(f'SELECT * FROM {_FAILURE_TABLE} ORDER BY count DESC, last_seen_at DESC').fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_tool_stats(self, tool_name: Optional[str]=None) -> dict[str, Any]:
        """Return tool usage statistics.

        Parameters
        ----------
        tool_name : str or None
            Specific tool to query.  If ``None``, returns stats for all tools.

        Returns
        -------
        dict
            If *tool_name* is specified: a single dict with tool stats (or
            an empty dict if not found).  If *tool_name* is ``None``: a dict
            with keys ``tools`` (list of per-tool dicts) and ``total_calls``.
            Each per-tool dict includes ``success_rate``.
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        if tool_name:
            row = conn.execute(f'\n                SELECT *, \n                    CASE WHEN call_count > 0\n                        THEN CAST(success_count AS REAL) / call_count\n                        ELSE 0.0\n                    END AS success_rate\n                FROM {_TOOL_TABLE} WHERE tool_name = ?\n                ', (tool_name,)).fetchone()
            return dict(row) if row else {}
        rows = conn.execute(f'\n            SELECT *,\n                CASE WHEN call_count > 0\n                    THEN CAST(success_count AS REAL) / call_count\n                    ELSE 0.0\n                END AS success_rate\n            FROM {_TOOL_TABLE}\n            ORDER BY call_count DESC\n            ').fetchall()
        tools = [_row_to_dict(r) for r in rows]
        total = sum((t.get('call_count', 0) for t in tools))
        return {'tools': tools, 'total_calls': total}

    def get_summary(self) -> dict[str, Any]:
        """Return a high-level summary of accumulated experience.

        Returns
        -------
        dict
            Keys:
            - ``total_successful_patterns`` (int)
            - ``total_failure_patterns`` (int)
            - ``total_tools_tracked`` (int)
            - ``total_tool_calls`` (int)
            - ``overall_tool_success_rate`` (float)
            - ``top_strategies`` (list[dict], top 5)
            - ``top_failures`` (list[dict], top 5)
            - ``top_tools`` (list[dict], top 5)
        """
        conn = self._schema_mgr.get_connection(_EXPERIENCE_DB)
        success_count = conn.execute(f'SELECT COUNT(*) AS cnt FROM {_SUCCESS_TABLE}').fetchone()['cnt']
        failure_count = conn.execute(f'SELECT COUNT(*) AS cnt FROM {_FAILURE_TABLE}').fetchone()['cnt']
        tool_rows = conn.execute(f'SELECT COUNT(*) AS cnt, COALESCE(SUM(call_count), 0) AS total_calls, COALESCE(SUM(success_count), 0) AS total_success FROM {_TOOL_TABLE}').fetchone()
        tools_tracked = tool_rows['cnt']
        total_calls = tool_rows['total_calls']
        total_success = tool_rows['total_success']
        overall_rate = round(total_success / total_calls * 100.0 if total_calls > 0 else 0.0, 2)
        top_strategies = [_row_to_dict(r) for r in conn.execute(f'SELECT * FROM {_SUCCESS_TABLE} ORDER BY success_count DESC LIMIT 5').fetchall()]
        top_failures = [_row_to_dict(r) for r in conn.execute(f'SELECT * FROM {_FAILURE_TABLE} ORDER BY count DESC LIMIT 5').fetchall()]
        top_tools = [_row_to_dict(r) for r in conn.execute(f'SELECT *, CASE WHEN call_count > 0   THEN CAST(success_count AS REAL) / call_count   ELSE 0.0 END AS success_rate FROM {_TOOL_TABLE} ORDER BY call_count DESC LIMIT 5').fetchall()]
        return {'total_successful_patterns': success_count, 'total_failure_patterns': failure_count, 'total_tools_tracked': tools_tracked, 'total_tool_calls': total_calls, 'overall_tool_success_rate': overall_rate, 'top_strategies': top_strategies, 'top_failures': top_failures, 'top_tools': top_tools}

def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a ``sqlite3.Row`` to a plain dictionary.

    JSON string columns (action_sequence, tags) are automatically parsed
    into Python objects where applicable.
    """
    if row is None:
        return {}
    data = dict(row)
    for json_col in ('action_sequence', 'tags'):
        if json_col in data and isinstance(data[json_col], str) and data[json_col]:
            try:
                data[json_col] = json.loads(data[json_col])
            except (json.JSONDecodeError, TypeError):
                pass
    return data

def get_experience() -> ExperienceManager:
    """Return the module-level ``ExperienceManager`` singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ExperienceManager()
        return _instance


def reset_experience_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None
def get_reflection_engine() -> Any:
    """Return the ``ReflectionEngine`` singleton (lazy import).

    Avoids circular import at module load time.  Tries relative package
    import first, then standalone direct import.
    """
    try:
        from importlib import import_module
        pkg = __package__ or 'hermes.core'
        mod = import_module(f'.{_REFLECTION_ENGINE_MODULE}', package=pkg)
    except (ImportError, ModuleNotFoundError):
        import sys as _sys
        _pkg_dir = os.path.dirname(os.path.abspath(__file__))
        if _pkg_dir not in _sys.path:
            _sys.path.insert(0, _pkg_dir)
        from reflection_engine import get_reflection as _gr
        return _gr()
    else:
        return mod.get_reflection()

def reflect_and_learn(task_id: str, goal: str, result: dict[str, Any], context: Optional[dict[str, Any]]=None) -> Any:
    """Convenience: reflect on a task result and learn from it in one call.

    Uses the module-level singletons for both ReflectionEngine and
    ExperienceManager.  The reflection is created and persisted; the
    experience manager records success/failure and tool usage if the
    result contains tool-level data.

    Parameters
    ----------
    task_id : str
        Identifier of the task.
    goal : str
        The task goal.
    result : dict
        Task result payload.
    context : dict or None
        Optional execution context.

    Returns
    -------
    Reflection
        The reflection produced by ``ReflectionEngine.reflect_on_task``.
    """
    rengine = get_reflection_engine()
    reflection = rengine.reflect_on_task(task_id, goal, result, context)
    em = get_experience()
    domain = (context or {}).get('domain') or (result or {}).get('domain') or 'general'
    tags = (context or {}).get('tags') or (result or {}).get('tags')
    if reflection.success:
        actions = []
        steps = (result or {}).get('steps') or (result or {}).get('actions') or []
        if isinstance(steps, list):
            for s in steps:
                if isinstance(s, dict):
                    actions.append(s.get('name') or s.get('action') or str(s))
                else:
                    actions.append(str(s))
        duration_s = (result or {}).get('duration_s', 0.0)
        em.record_success(pattern_name=goal[:200] if goal else f'task_{task_id[:8]}', action_sequence=actions or [goal[:100]], duration_s=duration_s, domain=domain, tags=tags)
    else:
        error_type = 'task_failure'
        error_msg = (result or {}).get('error') or (result or {}).get('error_message') or goal
        em.record_failure(domain=domain, error_type=error_type, error_message=str(error_msg))
    tools_used = (result or {}).get('tools_used') or (result or {}).get('tools') or []
    if isinstance(tools_used, list):
        for t in tools_used:
            if isinstance(t, dict):
                em.record_tool_usage(tool_name=t.get('name', 'unknown'), success=t.get('success', reflection.success), cost=t.get('cost', 0.0), duration_s=t.get('duration_s', 0.0))
            elif isinstance(t, str):
                em.record_tool_usage(tool_name=t, success=reflection.success)
    return reflection