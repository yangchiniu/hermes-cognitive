"""
plan_executor.py — Execute ``Plan`` objects step-by-step through the runtime.

Takes a ``Plan`` (or its dict form), resolves dependencies, calls
``execute_tool()`` for each step, applies fallbacks on failure, and
returns a structured execution log.

Usage
-----
    from plan_executor import execute_plan

    result = execute_plan(plan, runtime)

    # result = {
    #     "plan_id": "...",
    #     "goal": "...",
    #     "status": "completed" | "partial" | "failed",
    #     "steps": [
    #         {
    #             "step_id": "step_001",
    #             "action": "web_search",
    #             "status": "success",
    #             "output": "...",
    #             "duration_s": 1.2,
    #         },
    #         ...
    #     ],
    #     "fallback_used": True,
    #     "total_steps": 5,
    #     "completed_steps": 4,
    #     "failed_steps": 1,
    #     "total_duration_s": 12.3,
    # }
"""
from __future__ import annotations
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_PLAN_DEPTH = 10
_MAX_FALLBACK_RETRIES = 3
_STEP_TIMEOUT_S = 300  # 5 min per step


def _step_deps_met(step_id: str, plan_steps: List[Dict[str, Any]],
                   completed: set) -> bool:
    """Return True if all dependencies of *step_id* are in *completed*."""
    for s in plan_steps:
        if s["id"] == step_id:
            deps = s.get("dependencies", [])
            for dep_id in deps:
                if dep_id not in completed:
                    return False
            return True
    return True  # step not found — treat as ready


def _find_step(step_id: str, plan_steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for s in plan_steps:
        if s["id"] == step_id:
            return s
    return None


def _find_step_by_action(action: str, plan_steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for s in plan_steps:
        if s.get("action") == action:
            return s
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_plan(plan: Any, runtime: Any,
                 auto_execute: bool = True) -> Dict[str, Any]:
    """Execute a ``Plan`` (or its ``to_dict()`` equivalent) step by step.

    Parameters
    ----------
    plan : Plan or dict
        A ``Plan`` instance or a dict with ``steps``, ``fallbacks``,
        ``plan_id``, ``goal``, and ``constraints`` keys.
    runtime : RuntimeHotPath
        The ``RuntimeHotPath`` instance that provides ``execute_tool()``.
    auto_execute : bool
        If ``True``, automatically execute each step.  If ``False``,
        return the plan summary without executing.

    Returns
    -------
    dict
        Execution report (see module docstring for schema).
    """
    if not auto_execute:
        # Just return the plan schema without running
        return _summarize_plan(plan)

    # Normalise to dict
    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else (
        plan if isinstance(plan, dict) else {}
    )

    plan_id = plan_dict.get("plan_id", "")
    goal = plan_dict.get("goal", "")
    raw_steps = plan_dict.get("steps", [])
    fallbacks = plan_dict.get("fallbacks", {})
    constraints = plan_dict.get("constraints", {})
    max_depth = constraints.get("max_plan_depth", _MAX_PLAN_DEPTH)

    if not raw_steps:
        return {
            "plan_id": plan_id,
            "goal": goal,
            "status": "failed",
            "steps": [],
            "fallback_used": False,
            "total_steps": 0,
            "completed_steps": 0,
            "failed_steps": 0,
            "total_duration_s": 0.0,
            "error": "Plan has no steps",
        }

    # Limit depth
    steps_subset = raw_steps[:max_depth]
    step_results: List[Dict[str, Any]] = []
    completed: set = set()
    fallback_used = False
    has_failure = False
    total_start = time.monotonic()

    while len(completed) < len(steps_subset):
        # Find next executable step (deps met, not yet run)
        next_step = None
        for s in steps_subset:
            sid = s.get("id", "")
            if sid not in completed and _step_deps_met(sid, steps_subset, completed):
                next_step = s
                break

        if next_step is None:
            # No step is ready — dependency deadlock
            remaining = [s.get("id", "?") for s in steps_subset
                         if s.get("id", "") not in completed]
            logger.warning("plan_executor: dependency deadlock on %s", remaining)
            break

        step_id = next_step.get("id", "")
        action = next_step.get("action", "")
        params = next_step.get("params", {})

        # Try execution with fallback
        result = _run_single_step(step_id, action, params, runtime)

        if result["status"] == "success":
            completed.add(step_id)
        else:
            # Try fallback
            fb_id = fallbacks.get(step_id, "")
            if fb_id and fb_id not in completed:
                fb_step = _find_step(fb_id, steps_subset)
                if fb_step:
                    logger.info(
                        "plan_executor: step %s failed, trying fallback %s",
                        step_id, fb_id,
                    )
                    fb_action = fb_step.get("action", "")
                    fb_params = fb_step.get("params", {})
                    fb_result = _run_single_step(fb_id, fb_action, fb_params, runtime)
                    fallback_used = True
                    result = fb_result
                    if fb_result["status"] == "success":
                        completed.add(step_id)
                        completed.add(fb_id)

            if result["status"] != "success":
                has_failure = True
                completed.add(step_id)  # mark as done (failed) so we continue

        step_results.append(result)

    total_duration = time.monotonic() - total_start

    completed_count = sum(1 for r in step_results if r["status"] == "success")
    failed_count = sum(1 for r in step_results if r["status"] != "success")

    if not has_failure and completed_count == len(steps_subset):
        status = "completed"
    elif completed_count > 0:
        status = "partial"
    else:
        status = "failed"

    return {
        "plan_id": plan_id,
        "goal": goal,
        "status": status,
        "steps": step_results,
        "fallback_used": fallback_used,
        "total_steps": len(steps_subset),
        "completed_steps": completed_count,
        "failed_steps": failed_count,
        "total_duration_s": round(total_duration, 3),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_single_step(step_id: str, action: str,
                     params: Dict[str, Any],
                     runtime: Any) -> Dict[str, Any]:
    """Execute a single plan step via the runtime."""
    start = time.monotonic()
    result: Dict[str, Any] = {
        "step_id": step_id,
        "action": action,
        "status": "pending",
        "output": "",
        "error": "",
        "duration_s": 0.0,
    }

    if not action:
        result["status"] = "failed"
        result["error"] = "Step has no action"
        result["duration_s"] = time.monotonic() - start
        return result

    try:
        if not hasattr(runtime, "execute_tool"):
            result["status"] = "failed"
            result["error"] = "Runtime has no execute_tool method"
            result["duration_s"] = time.monotonic() - start
            return result

        exec_result = runtime.execute_tool(
            tool_name=action,
            args=params or {},
        )

        if exec_result is None:
            result["status"] = "failed"
            result["error"] = "execute_tool returned None"
        elif hasattr(exec_result, "success"):
            if exec_result.success:
                result["status"] = "success"
                result["output"] = str(exec_result.output or "")
            else:
                result["status"] = "failed"
                result["error"] = str(exec_result.error or "")
                if hasattr(exec_result, "duration_s"):
                    result["duration_s"] = exec_result.duration_s
            if hasattr(exec_result, "duration_s") and result["duration_s"] == 0.0:
                result["duration_s"] = exec_result.duration_s
        elif isinstance(exec_result, dict):
            if exec_result.get("success"):
                result["status"] = "success"
                result["output"] = str(exec_result.get("output", ""))
            else:
                result["status"] = "failed"
                result["error"] = str(exec_result.get("error", ""))
        else:
            result["status"] = "success"
            result["output"] = str(exec_result)

    except Exception as exc:
        logger.debug("plan_executor: step %s action %s: %s", step_id, action, exc)
        result["status"] = "failed"
        result["error"] = str(exc)

    result["duration_s"] = round(time.monotonic() - start, 3)
    return result


def _summarize_plan(plan: Any) -> Dict[str, Any]:
    """Return a human-readable summary of a Plan without executing."""
    plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else (
        plan if isinstance(plan, dict) else {}
    )
    steps = plan_dict.get("steps", [])
    return {
        "plan_id": plan_dict.get("plan_id", ""),
        "goal": plan_dict.get("goal", ""),
        "status": "planned",
        "step_count": len(steps),
        "actions": [s.get("action", "?") for s in steps],
        "estimated_cost": plan_dict.get("estimated_cost", {}),
        "risk_assessment": plan_dict.get("risk_assessment", {}),
        "fallbacks": dict(plan_dict.get("fallbacks", {})),
        "total_duration_s": 0.0,
    }
