"""reflection_engine.py — Post-task reflection system for Hermes Core.

Provides the Reflection dataclass and a singleton ReflectionEngine that
analyses task results, identifies mistakes and successes, suggests
improvements, and persists everything to reflection.db.

Standard library only: sqlite3, uuid, datetime, json, dataclasses, pathlib.
"""

from __future__ import annotations

import json
import threading
import uuid as _uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
import os

try:
    from .db_schema import get_manager as _get_schema_manager
    from .event_logger import get_logger
    from .exceptions import ReflectionError
except ImportError:
    import sys as _sys, os as _os
    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _pkg_dir not in _sys.path:
        _sys.path.insert(0, _pkg_dir)
    from db_schema import get_manager as _get_schema_manager
    from event_logger import get_logger
    from exceptions import ReflectionError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REFLECTION_DB = "reflection"
_REFLECTION_TABLE = "reflections"
_SESSION_ID = os.environ.get("HERMES_SESSION_ID", "unknown")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Reflection:
    """Structured output of a post-task reflection cycle.

    Attributes
    ----------
    reflection_id : str
        UUID for this reflection instance.
    session_id : str
        Hermes session identifier.
    task_id : str
        The task (node) being reflected upon.
    task_description : str
        Human-readable description of what the task was.
    goal : str
        The stated goal or objective of the task.
    result_summary : str
        Brief summary of what actually happened.
    success : bool
        Whether the task is considered successful.
    mistakes : list[str]
        Identified errors, missteps, or suboptimal decisions.
    improvements : list[str]
        Concrete suggestions for doing better next time.
    successful_patterns : list[str]
        Patterns that contributed to success (if any).
    created_at : str
        ISO-8601 UTC timestamp of when the reflection was created.
    """
    reflection_id: str
    session_id: str
    task_id: str
    task_description: str
    goal: str
    result_summary: str
    success: bool
    mistakes: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    successful_patterns: list[str] = field(default_factory=list)
    created_at: str = ""


# ---------------------------------------------------------------------------
# Singleton machinery
# ---------------------------------------------------------------------------

_instance: Optional["ReflectionEngine"] = None
_instance_lock = threading.Lock()


# ---------------------------------------------------------------------------
# ReflectionEngine
# ---------------------------------------------------------------------------


class ReflectionEngine:
    """Singleton engine for post-task reflection and self-improvement.

    Analyses task results, extracts mistakes and successful patterns, and
    persists structured reflections to reflection.db via SchemaManager.
    """

    def __init__(self, schema_manager: Any = None) -> None:
        """Initialise the reflection engine.

        Parameters
        ----------
        schema_manager : SchemaManager or None
            If provided, use this instance; otherwise acquire the module-level
            singleton via ``db_schema.get_manager()``.
        """
        if getattr(self, "_initialized", False):
            return

        self._schema_mgr = schema_manager if schema_manager is not None else _get_schema_manager()
        self._schema_mgr.initialize(_REFLECTION_DB)
        self._lock = threading.Lock()
        self._logger = get_logger()
        self._initialized = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reflect_on_task(
        self,
        task_id: str,
        goal: str,
        result: dict[str, Any],
        context: Optional[dict[str, Any]] = None,
    ) -> Reflection:
        """Analyse a task result, produce a structured reflection, and persist it.

        Parameters
        ----------
        task_id : str
            Identifier of the task to reflect on.
        goal : str
            The intended goal or objective.
        result : dict
            Result payload from task execution. Expected keys include:
            ``success``, ``summary``, ``description``, ``errors``,
            ``steps``, ``duration_s``, etc.
        context : dict or None
            Optional execution context (session info, environment, etc.).

        Returns
        -------
        Reflection
            The newly created and persisted reflection.
        """
        context = context or {}
        result_data = result or {}

        # --- Extract metadata ---
        task_description = result_data.get("description", "") or ""
        result_summary = result_data.get("summary", "") or ""
        success = bool(result_data.get("success", False))

        # --- Analysis ---
        mistakes = self._analyze_mistakes(result_data, context)
        improvements = self._generate_improvements(goal, mistakes)
        successful_patterns = self._extract_patterns(goal, result_data)

        # --- Build reflection ---
        now = datetime.now(timezone.utc).isoformat()
        reflection = Reflection(
            reflection_id=str(_uuid.uuid4()),
            session_id=_SESSION_ID,
            task_id=task_id,
            task_description=task_description,
            goal=goal,
            result_summary=result_summary,
            success=success,
            mistakes=mistakes,
            improvements=improvements,
            successful_patterns=successful_patterns,
            created_at=now,
        )

        # --- Persist ---
        self.save_reflection(reflection)

        # --- Log ---
        self._logger.log(
            "reflection.created",
            {
                "reflection_id": reflection.reflection_id,
                "task_id": task_id,
                "success": success,
                "mistake_count": len(mistakes),
                "improvement_count": len(improvements),
            },
        )

        return reflection

    def get_reflection(self, task_id: str) -> Optional[Reflection]:
        """Retrieve the most recent reflection for a given *task_id*.

        Parameters
        ----------
        task_id : str
            Task identifier to look up.

        Returns
        -------
        Reflection or None
            The latest reflection for this task, or ``None`` if none exists.
        """
        conn = self._schema_mgr.get_connection(_REFLECTION_DB)
        cursor = conn.execute(
            "SELECT * FROM reflections WHERE task_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        row = cursor.fetchone()
        return self._row_to_reflection(row) if row else None

    def get_recent_reflections(self, limit: int = 10) -> list[Reflection]:
        """Return the most recent reflections across all tasks.

        Parameters
        ----------
        limit : int
            Maximum number of reflections to return (default 10).

        Returns
        -------
        list[Reflection]
            Reflections ordered by creation time, newest first.
        """
        conn = self._schema_mgr.get_connection(_REFLECTION_DB)
        cursor = conn.execute(
            "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_reflection(row) for row in cursor.fetchall()]

    def get_failure_patterns(self) -> list[dict[str, Any]]:
        """Aggregate failure patterns across all reflections.

        Returns
        -------
        list[dict]
            Each dict: ``{"mistake": str, "count": int, "recent": str}``,
            ordered by count descending.
        """
        conn = self._schema_mgr.get_connection(_REFLECTION_DB)
        cursor = conn.execute(
            "SELECT mistakes, created_at FROM reflections WHERE success = 0 "
            "ORDER BY created_at DESC"
        )
        mistake_counter: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            raw = row["mistakes"]
            try:
                items = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                items = []
            for m in items:
                m_str = str(m)
                if m_str not in mistake_counter:
                    mistake_counter[m_str] = {"mistake": m_str, "count": 0, "recent": ""}
                mistake_counter[m_str]["count"] += 1
                if not mistake_counter[m_str]["recent"]:
                    mistake_counter[m_str]["recent"] = row["created_at"] if row["created_at"] else ""

        return sorted(mistake_counter.values(), key=lambda x: x["count"], reverse=True)

    def get_success_patterns(self) -> list[dict[str, Any]]:
        """Aggregate successful patterns across all reflections.

        Returns
        -------
        list[dict]
            Each dict: ``{"pattern": str, "count": int, "recent": str}``,
            ordered by count descending.
        """
        conn = self._schema_mgr.get_connection(_REFLECTION_DB)
        cursor = conn.execute(
            "SELECT successful_patterns, created_at FROM reflections "
            "WHERE success = 1 ORDER BY created_at DESC"
        )
        pattern_counter: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            raw = row["successful_patterns"]
            try:
                items = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                items = []
            for p in items:
                p_str = str(p)
                if p_str not in pattern_counter:
                    pattern_counter[p_str] = {"pattern": p_str, "count": 0, "recent": ""}
                pattern_counter[p_str]["count"] += 1
                if not pattern_counter[p_str]["recent"]:
                    pattern_counter[p_str]["recent"] = row["created_at"] if row["created_at"] else ""

        return sorted(pattern_counter.values(), key=lambda x: x["count"], reverse=True)

    def get_stats(self) -> dict[str, Any]:
        """Return high-level reflection statistics.

        Returns
        -------
        dict
            Keys: ``total_reflections``, ``successful``, ``failed``,
            ``success_rate``, ``common_mistakes`` (top-5), ``common_patterns``
            (top-5).
        """
        conn = self._schema_mgr.get_connection(_REFLECTION_DB)
        total = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reflections"
        ).fetchone()["cnt"]

        successful = conn.execute(
            "SELECT COUNT(*) AS cnt FROM reflections WHERE success = 1"
        ).fetchone()["cnt"]

        failed = total - successful
        success_rate = (successful / total * 100.0) if total > 0 else 0.0

        top_mistakes = self.get_failure_patterns()[:5]
        top_patterns = self.get_success_patterns()[:5]

        return {
            "total_reflections": total,
            "successful": successful,
            "failed": failed,
            "success_rate": round(success_rate, 2),
            "common_mistakes": top_mistakes,
            "common_patterns": top_patterns,
        }

    def save_reflection(self, r: Reflection) -> None:
        """Persist a Reflection to reflection.db.

        Parameters
        ----------
        r : Reflection
            The reflection to save. Upsert by ``reflection_id``.
        """
        conn = self._schema_mgr.get_connection(_REFLECTION_DB)
        with self._lock:
            conn.execute(
                """
                INSERT OR REPLACE INTO reflections
                    (session_id, task_id, task_description, goal,
                     result_summary, success, mistakes, improvements,
                     successful_patterns, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r.session_id,
                    r.task_id,
                    r.task_description,
                    r.goal,
                    r.result_summary,
                    int(r.success),
                    json.dumps(r.mistakes, ensure_ascii=False),
                    json.dumps(r.improvements, ensure_ascii=False),
                    json.dumps(r.successful_patterns, ensure_ascii=False),
                    r.created_at,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Internal analysis helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _analyze_mistakes(
        result: dict[str, Any],
        context: dict[str, Any],
    ) -> list[str]:
        """Analyse a task result and extract a list of mistake descriptions.

        Examines error messages, failed steps, exception traces, and
        context signals to compile a human-readable list of what went wrong.

        Parameters
        ----------
        result : dict
            Task result payload.
        context : dict
            Execution context (may contain additional error signals).

        Returns
        -------
        list[str]
            Identified mistake descriptions, or an empty list if none found.
        """
        mistakes: list[str] = []

        # 1. Check top-level error fields
        error = result.get("error") or result.get("error_message") or ""
        if error:
            mistakes.append(f"Error reported: {error}")

        # 2. Check for errors array
        errors = result.get("errors") or result.get("error_list") or []
        if isinstance(errors, list):
            for e in errors:
                msg = ""
                if isinstance(e, dict):
                    msg = e.get("message") or e.get("error") or str(e)
                else:
                    msg = str(e)
                if msg and msg not in mistakes:
                    mistakes.append(msg)

        # 3. Check failed steps
        steps = result.get("steps") or result.get("actions") or []
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    step_status = step.get("status") or step.get("success")
                    if step_status in (False, "failed", "error"):
                        step_name = step.get("name") or step.get("action") or "unknown step"
                        step_error = step.get("error") or step.get("message") or ""
                        if step_error:
                            mistakes.append(
                                f"Step '{step_name}' failed: {step_error}"
                            )
                        else:
                            mistakes.append(f"Step '{step_name}' failed")

        # 4. Check context for additional error signals
        ctx_errors = context.get("errors") or context.get("failures") or []
        if isinstance(ctx_errors, list):
            for ce in ctx_errors:
                msg = str(ce) if not isinstance(ce, dict) else str(ce.get("message", ce))
                if msg and msg not in mistakes:
                    mistakes.append(msg)

        # 5. Check for timeout indicators
        if result.get("timed_out") or result.get("timeout"):
            mistakes.append("Task timed out before completion")

        # 6. Check for partial results (task completed but with caveats)
        if result.get("partial", False):
            mistakes.append("Task produced only a partial result")

        return mistakes

    @staticmethod
    def _generate_improvements(
        goal: str,
        mistakes: list[str],
    ) -> list[str]:
        """Generate concrete improvement suggestions based on mistakes and goal.

        Maps common mistake patterns to actionable improvement advice.

        Parameters
        ----------
        goal : str
            The original goal of the task.
        mistakes : list[str]
            Mistake descriptions from ``_analyze_mistakes``.

        Returns
        -------
        list[str]
            Improvement suggestions, or a generic suggestion if none apply.
        """
        improvements: list[str] = []

        mistake_text = " ".join(mistakes).lower()

        # Timeout
        if "timeout" in mistake_text or "timed out" in mistake_text:
            improvements.append(
                "Increase timeout or break the task into smaller sub-tasks"
            )

        # Network / connectivity
        if any(w in mistake_text for w in ("network", "connection", "dns", "timeout")):
            improvements.append(
                "Add retry logic with exponential backoff for network operations"
            )

        # Authentication / authorization
        if any(w in mistake_text for w in ("auth", "login", "permission", "forbidden", "unauthorized")):
            improvements.append(
                "Verify credentials and permissions before attempting the operation"
            )

        # Validation / parsing
        if any(w in mistake_text for w in ("parse", "invalid", "validation", "schema", "format")):
            improvements.append(
                "Validate input/output format early and use schema-aware parsing"
            )

        # Resource limits
        if any(w in mistake_text for w in ("memory", "disk", "quota", "rate limit", "too many")):
            improvements.append(
                "Monitor resource usage and implement throttling or batching"
            )

        # Partial result
        if "partial" in mistake_text:
            improvements.append(
                "Add checkpointing so partial progress is not lost on failure"
            )

        # Generic fallback if few improvements were generated
        if len(improvements) < 2:
            improvements.append(
                "Add more granular error handling and logging for this task type"
            )

        if len(improvements) < 3:
            improvements.append(
                f"Consider a different approach or decompose '{goal}' into smaller goals"
            )

        return improvements

    @staticmethod
    def _extract_patterns(
        goal: str,
        result: dict[str, Any],
    ) -> list[str]:
        """Extract successful patterns from a task result.

        Identifies reusable strategies, approaches, or configurations that
        contributed to success.

        Parameters
        ----------
        goal : str
            The original goal of the task.
        result : dict
            Task result payload.

        Returns
        -------
        list[str]
            Descriptions of successful patterns, or an empty list.
        """
        patterns: list[str] = []

        # Mark task itself as a pattern if it succeeded
        if result.get("success"):
            description = result.get("description") or goal
            if description:
                patterns.append(f"Executed task: {description}")

        # Check for explicit pattern field
        raw_patterns = result.get("patterns") or result.get("successful_patterns") or []
        if isinstance(raw_patterns, list):
            for p in raw_patterns:
                p_str = str(p) if not isinstance(p, dict) else p.get("pattern", str(p))
                if p_str and p_str not in patterns:
                    patterns.append(p_str)

        # Check for strategy metadata
        strategy = result.get("strategy") or result.get("approach")
        if strategy:
            patterns.append(f"Strategy used: {strategy}")

        # Check for tool usage patterns
        tools_used = result.get("tools_used") or result.get("tools") or []
        if isinstance(tools_used, list) and tools_used:
            tool_names = [t if isinstance(t, str) else t.get("name", str(t)) for t in tools_used]
            patterns.append(f"Effective tool chain: {', '.join(tool_names)}")

        # Check for successful sub-steps
        steps = result.get("steps") or result.get("actions") or []
        if isinstance(steps, list):
            successful_steps = [
                s for s in steps
                if isinstance(s, dict) and s.get("status") in (True, "completed", "success")
            ]
            for s_step in successful_steps:
                s_name = s_step.get("name") or s_step.get("action") or ""
                if s_name:
                    s_detail = s_step.get("detail") or s_step.get("summary") or ""
                    if s_detail:
                        patterns.append(f"Step '{s_name}': {s_detail}")
                    else:
                        patterns.append(f"Step '{s_name}' completed successfully")

        return patterns

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_reflection(row: sqlite3.Row) -> Reflection:  # type: ignore[name-defined]
        """Convert a ``sqlite3.Row`` to a ``Reflection`` dataclass instance."""
        return Reflection(
            reflection_id=row["reflection_id"] or "",
            session_id=row["session_id"] or "",
            task_id=row["task_id"] or "",
            task_description=row["task_description"] or "",
            goal=row["goal"] or "",
            result_summary=row["result_summary"] or "",
            success=bool(row["success"]),
            mistakes=_json_loads(row["mistakes"]),
            improvements=_json_loads(row["improvements"]),
            successful_patterns=_json_loads(row["successful_patterns"]),
            created_at=row["created_at"] or "",
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _json_loads(raw: Any) -> list[str]:
    """Safely parse a JSON string into a list of strings."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def get_reflection() -> ReflectionEngine:
    """Return the module-level ``ReflectionEngine`` singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = ReflectionEngine()
        return _instance


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

get_engine = get_reflection

def reset_reflection_instance():
    """Reset singleton for testing or config change."""
    global _instance
    with _instance_lock:
        _instance = None
