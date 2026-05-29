"""
24-Hour Stability Test

Runs endurance testing for Hermes Core by periodically creating random tasks,
snapshotting resources, checking memory/threads, and logging metrics.

Can be run for any duration (default 24 hours) or in simulation mode for CI.

Usage:
    from hermes.core.tests.stability.run_stability import run_stability_test
    summary = run_stability_test(duration_hours=1)
"""

import os
import time
import json
import random
import string
import sys
import threading
import traceback
from pathlib import Path

try:
    from .metrics import MetricsTracker, _sample_resources, create_tracker
except ImportError:
    from metrics import MetricsTracker, _sample_resources, create_tracker

try:
    from hermes.core.kernel import Kernel
    HAS_KERNEL = True
except ImportError:
    HAS_KERNEL = False

try:
    from hermes.core.event_bus import EventBus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False

try:
    from hermes.core.ooda import OODALoop
    HAS_OODA = True
except ImportError:
    HAS_OODA = False


# ---------------------------------------------------------------------------
# Random task generators
# ---------------------------------------------------------------------------

RANDOM_TASKS = [
    "search for latest technology news",
    "summarize the current weather in Tokyo",
    "find open-source Python projects for data visualization",
    "read config.yaml and extract database settings",
    "search for affordable hotels in Paris",
    "scrape the top 10 stories from Hacker News",
    "check disk usage and report if above 80%",
    "find the best restaurants in Kunming",
    "search for AI safety research papers",
    "list all files in /tmp and count them",
    "search for Python async programming best practices",
    "find the current time in multiple timezones",
    "search for machine learning tutorials for beginners",
    "look up the population of major world cities",
    "find the cheapest flights to Bangkok next month",
    "search for Kubernetes deployment strategies",
    "look up recent advances in natural language processing",
    "find the top-rated books on system design",
    "search for Rust vs Go performance benchmarks",
    "find the latest version of Python and its features",
]


def generate_random_task():
    """Return a random task string."""
    return random.choice(RANDOM_TASKS)


# ---------------------------------------------------------------------------
# Helper: format duration
# ---------------------------------------------------------------------------

def _format_duration(seconds):
    """Format seconds into human-readable string."""
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


# ---------------------------------------------------------------------------
# Main stability test
# ---------------------------------------------------------------------------

def run_stability_test(duration_hours=24, interval_minutes=5, verbose=False, report_path=None):
    """
    Run a stability/endurance test.

    Args:
        duration_hours: How long to run (default 24).
        interval_minutes: How often to snapshot and create tasks (default 5).
        verbose: Print detailed output.
        report_path: Where to write the final report JSON.

    Returns:
        dict with keys: duration_hours, cycles_completed, tasks_created,
                        tasks_succeeded, tasks_failed, resource_summary,
                        leak_warnings, passed
    """
    duration_seconds = duration_hours * 3600
    interval_seconds = interval_minutes * 60
    end_time = time.time() + duration_seconds

    if duration_seconds < 60:
        # For very short tests, adjust interval
        interval_seconds = max(1, duration_seconds // 5)
        if verbose:
            print(f"  [stability] Short test: interval adjusted to {interval_seconds}s")

    print(f"\n{'=' * 60}")
    print(f"STABILITY TEST — {duration_hours}h duration, {interval_minutes}min interval")
    print(f"{'=' * 60}")
    print(f"  Start time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  End time:   {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_time))}")
    print(f"{'=' * 60}")

    # Initialize
    tracker = create_tracker()
    cycle_count = 0
    tasks_created = 0
    tasks_succeeded = 0
    tasks_failed = 0
    errors = []

    # Initialize core components if available
    kernel = None
    event_bus = None
    ooda = None

    if HAS_KERNEL:
        try:
            kernel = Kernel()
            if verbose:
                print("  [stability] Kernel initialized")
        except Exception as e:
            if verbose:
                print(f"  [stability] Kernel init skipped: {e}")

    if HAS_EVENT_BUS:
        try:
            event_bus = EventBus()
            if verbose:
                print("  [stability] EventBus initialized")
        except Exception as e:
            if verbose:
                print(f"  [stability] EventBus init skipped: {e}")

    if HAS_OODA:
        try:
            ooda = OODALoop()
            if verbose:
                print("  [stability] OODALoop initialized")
        except Exception as e:
            if verbose:
                print(f"  [stability] OODALoop init skipped: {e}")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    start_wall = time.time()
    next_cycle = start_wall

    while time.time() < end_time:
        cycle_count += 1
        now = time.time()
        remaining = end_time - now
        progress_pct = (now - start_wall) / duration_seconds * 100

        if verbose or cycle_count % 5 == 1:
            print(f"\n  [stability] Cycle {cycle_count} | "
                  f"{_format_duration(remaining)} remaining "
                  f"({progress_pct:.0f}%)")

        # 1. Snapshot resources
        try:
            resources = _sample_resources()
            tracker.record(resources)

            if verbose:
                rss_mb = resources.get("memory_rss", 0) / (1024 * 1024)
                threads = resources.get("threads", "?")
                print(f"    resources: RSS={rss_mb:.1f}MB, threads={threads}")
        except Exception as e:
            if verbose:
                print(f"    resource snapshot failed: {e}")

        # 2. Create and execute a random task
        task = generate_random_task()
        tasks_created += 1

        try:
            if verbose:
                print(f"    task: \"{task[:60]}...\"")
            # Execute through OODA if available
            if ooda:
                result = ooda.run_cycle(task)
                if result.get("outcome", {}).get("success", False):
                    tasks_succeeded += 1
                else:
                    tasks_failed += 1
                    errors.append({"cycle": cycle_count, "task": task, "phase": "act",
                                   "error": str(result.get("outcome", {}).get("errors", "unknown"))})
            elif kernel:
                # Fall back to kernel
                try:
                    result = kernel.execute(task)
                    tasks_succeeded += 1
                except Exception as e:
                    tasks_failed += 1
                    errors.append({"cycle": cycle_count, "task": task, "phase": "kernel", "error": str(e)})
            else:
                # Simulate execution
                time.sleep(random.uniform(0.01, 0.05))
                if random.random() < 0.9:  # 90% success
                    tasks_succeeded += 1
                else:
                    tasks_failed += 1
                    errors.append({"cycle": cycle_count, "task": task, "phase": "simulated",
                                   "error": "Simulated random failure"})
        except Exception as e:
            tasks_failed += 1
            errors.append({"cycle": cycle_count, "task": task, "phase": "unknown", "error": str(e)})

        # 3. Check for memory/thread leaks every 10 cycles
        if cycle_count % 10 == 0 and tracker.sample_count >= 10:
            leak_warnings = tracker.detect_leak()
            if leak_warnings:
                for w in leak_warnings:
                    print(f"    [leak-warning] {w}")

        # 4. Periodic summary
        if cycle_count % 20 == 0:
            success_rate = (tasks_succeeded / tasks_created * 100) if tasks_created > 0 else 0
            print(f"  [stability] Interim: {tasks_created} tasks, "
                  f"{success_rate:.0f}% success, "
                  f"{len(errors)} errors")

        # Wait until next cycle
        next_cycle += interval_seconds
        wait_time = next_cycle - time.time()
        if wait_time > 0:
            time.sleep(min(wait_time, 1.0))  # Wake periodically to check end_time
        elif wait_time < -interval_seconds:
            # Fell behind — reset next_cycle
            next_cycle = time.time() + interval_seconds

    # ------------------------------------------------------------------
    # Completion
    # ------------------------------------------------------------------
    actual_duration = time.time() - start_wall
    success_rate = (tasks_succeeded / tasks_created * 100) if tasks_created > 0 else 0

    print(f"\n{'=' * 60}")
    print(f"STABILITY TEST COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Actual duration:     {_format_duration(actual_duration)}")
    print(f"  Cycles completed:    {cycle_count}")
    print(f"  Tasks created:       {tasks_created}")
    print(f"  Tasks succeeded:     {tasks_succeeded} ({success_rate:.1f}%)")
    print(f"  Tasks failed:        {tasks_failed}")
    print(f"  Errors logged:       {len(errors)}")

    # Get resource summary
    resource_summary = tracker.get_summary()
    leak_warnings = tracker.detect_leak()

    if resource_summary:
        mem = resource_summary.get("memory_rss", {})
        if mem:
            print(f"\n  Resource summary:")
            print(f"    Memory RSS: min={mem.get('min', 0)/(1024*1024):.1f}MB, "
                  f"max={mem.get('max', 0)/(1024*1024):.1f}MB, "
                  f"avg={mem.get('avg', 0)/(1024*1024):.1f}MB")
            print(f"    Memory trend: {mem.get('trend_direction', 'N/A')}")

        thr = resource_summary.get("threads", {})
        if thr:
            print(f"    Threads: min={thr.get('min')}, max={thr.get('max')}, "
                  f"avg={thr.get('avg', 0):.1f}")
            print(f"    Thread trend: {thr.get('trend_direction', 'N/A')}")

    if leak_warnings:
        print(f"\n  [LEAK WARNINGS]")
        for w in leak_warnings:
            print(f"    - {w}")

    passed = (
        tasks_failed == 0
        and success_rate >= 90
        and len(leak_warnings) == 0
    )

    # Export report
    if report_path:
        tracker.export(report_path)
        print(f"\n  Metrics exported to: {report_path}")

    # Build result
    result = {
        "duration_hours": duration_hours,
        "actual_duration_s": round(actual_duration, 2),
        "cycles_completed": cycle_count,
        "tasks_created": tasks_created,
        "tasks_succeeded": tasks_succeeded,
        "tasks_failed": tasks_failed,
        "success_rate": round(success_rate, 1),
        "passed": passed,
        "resource_summary": resource_summary,
        "leak_warnings": leak_warnings,
        "errors": errors[:50],  # Cap at 50
        "error_count": len(errors),
    }

    return result


def run_quick_stability_test(minutes=5, verbose=True):
    """
    Run a quick stability test for CI/smoke testing.

    Args:
        minutes: Duration in minutes.
        verbose: Print detailed output.

    Returns:
        dict with stability results.
    """
    return run_stability_test(
        duration_hours=minutes / 60,
        interval_minutes=max(0.5, minutes / 10),
        verbose=verbose,
    )


if __name__ == "__main__":
    import sys
    hours = 24
    if len(sys.argv) > 1:
        try:
            hours = float(sys.argv[1])
        except ValueError:
            print(f"Usage: python run_stability.py [duration_hours=24]")
            sys.exit(1)
    run_stability_test(duration_hours=hours, verbose=True)
