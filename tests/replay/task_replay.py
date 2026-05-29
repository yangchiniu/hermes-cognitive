"""
Task Replay

Reads events from an event log, extracts completed tasks, re-executes them,
and compares results to detect regressions.

Standard library only; imports core modules with try/except guards.
"""

import os
import time
import json
import hashlib
import sys
from pathlib import Path

try:
    from .comparator import compare_results
except ImportError:
    from comparator import compare_results

try:
    from hermes.core.event_logger import EventLogger
    HAS_EVENT_LOGGER = True
except ImportError:
    HAS_EVENT_LOGGER = False

try:
    from hermes.core.kernel import Kernel
    HAS_KERNEL = True
except ImportError:
    HAS_KERNEL = False

try:
    from hermes.core.ooda import OODALoop
    HAS_OODA = True
except ImportError:
    HAS_OODA = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_event_log(log_path):
    """
    Parse a JSONL event log and return list of event dicts.

    Args:
        log_path: Path to .jsonl file.

    Returns:
        list of event dicts.
    """
    events = []
    path = Path(log_path)

    if not path.exists():
        return events

    content = path.read_text()
    for line_num, line in enumerate(content.strip().split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            event["_line"] = line_num
            events.append(event)
        except json.JSONDecodeError as e:
            print(f"  [replay] Warning: JSON parse error at {log_path}:{line_num}: {e}")
            continue

    return events


def _extract_completed_tasks(events, max_tasks=10):
    """
    Extract completed task records from event history.

    Looks for task.completed or plan.completed events with result data.

    Args:
        events: List of event dicts.
        max_tasks: Maximum number of tasks to extract.

    Returns:
        list of task dicts with keys: goal, original_result, timestamp, event_id
    """
    tasks = []

    for event in events:
        event_type = event.get("event_type", event.get("type", ""))
        data = event.get("data", event.get("payload", {}))

        # Look for completed task events
        is_task_event = any(kw in event_type.lower() for kw in ["task.completed", "plan.completed",
                                                                  "cycle.completed", "ooda.complete"])
        if not is_task_event:
            continue

        # Extract goal
        goal = (
            data.get("goal")
            or data.get("input")
            or data.get("task")
            or data.get("description")
            or str(data.get("args", {}))
        )

        if not goal:
            continue

        # Skip if goal looks like a system event
        if isinstance(goal, str) and goal.startswith("_"):
            continue

        task_entry = {
            "goal": str(goal)[:500],  # Cap length
            "original_result": data,
            "timestamp": data.get("timestamp", event.get("timestamp", 0)),
            "event_id": event.get("id", event.get("_line", 0)),
            "event_type": event_type,
            "source_log": event.get("_source", ""),
        }

        tasks.append(task_entry)

        if len(tasks) >= max_tasks:
            break

    return tasks


def _compute_result_hash(result):
    """Compute a stable hash of a result dict for comparison."""
    serialized = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def _simulate_execution(goal):
    """
    Simulate task execution when real core modules aren't available.
    Returns a deterministic result based on goal content.
    """
    time.sleep(0.01)
    # Produce a deterministic result
    return {
        "goal": goal,
        "success": True,
        "output": f"Simulated result for: {goal[:50]}...",
        "steps_completed": 2,
        "duration_s": 0.01,
        "_simulated": True,
    }


# ---------------------------------------------------------------------------
# Main replay function
# ---------------------------------------------------------------------------

def replay_tasks(event_log_path, max_tasks=10, verbose=False):
    """
    Read events from a log file, extract completed tasks, re-execute,
    and compare results.

    Args:
        event_log_path: Path to event log (.jsonl) file or directory of logs.
        max_tasks: Max number of tasks to replay.
        verbose: Print detailed output.

    Returns:
        dict with keys: replayed, success, failed, regression, results
    """
    log_path = Path(event_log_path)

    # Can be a single file or directory
    if log_path.is_dir():
        log_files = sorted(log_path.glob("*.jsonl"))
    elif log_path.is_file():
        log_files = [log_path]
    else:
        return {
            "replayed": 0,
            "success": 0,
            "failed": 0,
            "regression": [],
            "results": [],
            "error": f"Path not found: {event_log_path}",
        }

    if not log_files:
        return {
            "replayed": 0,
            "success": 0,
            "failed": 0,
            "regression": [],
            "results": [],
            "error": f"No .jsonl files found in {event_log_path}",
        }

    print(f"\n{'=' * 60}")
    print(f"TASK REPLAY")
    print(f"{'=' * 60}")
    print(f"  Source: {event_log_path}")
    print(f"  Max tasks: {max_tasks}")

    # Initialize core components if available
    kernel = None
    ooda = None
    if HAS_KERNEL:
        try:
            kernel = Kernel()
        except Exception:
            pass
    if HAS_OODA:
        try:
            ooda = OODALoop()
        except Exception:
            pass

    all_events = []
    for lf in log_files:
        events = _parse_event_log(lf)
        # Mark source
        for e in events:
            e["_source"] = str(lf)
        all_events.extend(events)

    if not all_events:
        return {
            "replayed": 0,
            "success": 0,
            "failed": 0,
            "regression": [],
            "results": [],
            "error": "No events found in log files",
        }

    print(f"  Events loaded: {len(all_events)}")

    # Extract completed tasks
    tasks = _extract_completed_tasks(all_events, max_tasks)

    if not tasks:
        print("  No completed tasks found in event log. Looking for any task events...")
        # Fallback: try to extract any events that look task-like
        for event in all_events[:max_tasks]:
            goal = (
                event.get("data", {}).get("goal")
                or event.get("data", {}).get("input")
                or str(event.get("event_type", event.get("type", "unknown")))
            )
            tasks.append({
                "goal": str(goal)[:500],
                "original_result": event.get("data", {}),
                "timestamp": event.get("timestamp", 0),
                "event_id": event.get("_line", 0),
                "source_log": event.get("_source", ""),
            })

    print(f"  Tasks extracted: {len(tasks)}")

    # Replay each task
    results_list = []
    replayed = 0
    succeeded = 0
    failed = 0
    regressions = []

    for i, task in enumerate(tasks):
        replayed += 1
        goal = task["goal"]
        original = task["original_result"]

        if verbose:
            print(f"\n  [{i+1}/{len(tasks)}] Replaying: \"{goal[:80]}...\"")

        # Execute
        t0 = time.time()
        try:
            if ooda:
                replay_result = ooda.run_cycle(goal)
                outcome = replay_result.get("outcome", {})
                replay_success = outcome.get("success", False)
            elif kernel:
                replay_result = kernel.execute(goal)
                replay_success = True
            else:
                replay_result = _simulate_execution(goal)
                replay_success = replay_result.get("success", False)
        except Exception as e:
            replay_result = {"error": str(e), "success": False}
            replay_success = False

        duration = time.time() - t0

        # Compare
        comparison = compare_results(original, replay_result)

        if replay_success and comparison.get("match", False):
            succeeded += 1
            status = "PASS"
        elif not replay_success:
            failed += 1
            status = "FAIL"
        else:
            # Executed but results differ — possible regression
            diff = comparison.get("differences", [])
            regressions.append({
                "goal": goal[:100],
                "differences": diff[:10],
                "original_hash": comparison.get("original_hash"),
                "replay_hash": comparison.get("replay_hash"),
            })
            status = "REGRESSION"

        if verbose:
            print(f"    Result: {status} ({duration:.2f}s)")
            if comparison.get("match"):
                print(f"    Hash match: {comparison.get('replay_hash')}")
            elif comparison.get("differences"):
                for d in comparison["differences"][:3]:
                    print(f"    Diff: {d}")

        results_list.append({
            "goal": goal[:200],
            "status": status,
            "duration_s": round(duration, 3),
            "original_hash": comparison.get("original_hash"),
            "replay_hash": comparison.get("replay_hash"),
            "match": comparison.get("match", False),
            "differences": comparison.get("differences", [])[:10],
        })

    # Summary
    regression_count = len(regressions)
    print(f"\n{'-' * 40}")
    print(f"Replay Summary:")
    print(f"  Tasks replayed: {replayed}")
    print(f"  Succeeded:      {succeeded}")
    print(f"  Failed:         {failed}")
    print(f"  Regressions:    {regression_count}")
    print(f"{'-' * 40}")

    return {
        "replayed": replayed,
        "success": succeeded,
        "failed": failed,
        "regression": regressions,
        "results": results_list,
    }


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def replay_from_default_log(max_tasks=10, verbose=False):
    """
    Replay tasks from the default Hermes event log location.

    Returns replay_tasks() result or a not-found message.
    """
    default_log = Path.home() / ".hermes" / "logs" / "events.jsonl"
    if default_log.exists():
        return replay_tasks(str(default_log), max_tasks=max_tasks, verbose=verbose)
    return {
        "replayed": 0,
        "success": 0,
        "failed": 0,
        "regression": [],
        "error": f"Default log not found: {default_log}",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        log_path = sys.argv[1]
        max_tasks = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    else:
        log_path = str(Path.home() / ".hermes" / "logs" / "events.jsonl")
        max_tasks = 10
        print(f"Usage: python task_replay.py <event_log_path> [max_tasks=10]")
        print(f"Using default: {log_path}")

    results = replay_tasks(log_path, max_tasks=max_tasks, verbose=True)
    print(f"\nReplay results: {results.get('replayed', 0)} replayed, "
          f"{results.get('success', 0)} passed, "
          f"{results.get('failed', 0)} failed")
