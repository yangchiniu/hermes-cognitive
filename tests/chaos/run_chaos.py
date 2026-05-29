"""
Chaos Test Runner

Runs chaos engineering scenarios against Hermes Core to verify resilience
and recovery behavior under various failure conditions.

Usage:
    from hermes.core.tests.chaos.run_chaos import run_chaos_scenario, run_all_chaos_tests
    results = run_all_chaos_tests()
"""

import time
import random
import sys
import traceback
from pathlib import Path

try:
    from .scenarios import ALL_SCENARIOS, SCENARIO_NAMES, ScenarioResult
    from .verify import verify_system_integrity, verify_recovery
except ImportError:
    # Direct import support
    from scenarios import ALL_SCENARIOS, SCENARIO_NAMES, ScenarioResult
    from verify import verify_system_integrity, verify_recovery

try:
    from hermes.core.kernel import Kernel
    HAS_KERNEL = True
except ImportError:
    HAS_KERNEL = False

try:
    from hermes.core.plan import Plan
    HAS_PLAN = True
except ImportError:
    HAS_PLAN = False

try:
    from hermes.core.event_bus import EventBus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False


def run_chaos_scenario(name, world_model=None, plan=None, event_bus=None):
    """
    Run a single chaos scenario and return (passed, details).

    Args:
        name: Scenario name from ALL_SCENARIOS keys.
        world_model: World model instance (or stub if unavailable).
        plan: Plan instance (or stub if unavailable).
        event_bus: Event bus instance (or stub if unavailable).

    Returns:
        dict with keys: scenario, passed, recovery_triggered,
                        recovery_success, duration_s, details
    """
    if name not in ALL_SCENARIOS:
        return {
            "scenario": name,
            "passed": False,
            "recovery_triggered": False,
            "recovery_success": False,
            "duration_s": 0.0,
            "details": f"Unknown scenario: {name}. Available: {list(ALL_SCENARIOS.keys())}",
        }

    scenario_fn = ALL_SCENARIOS[name]

    # Build real or stub dependencies
    if HAS_KERNEL and world_model is None:
        try:
            world_model = Kernel()
        except Exception:
            from .scenarios import StubWorldModel
            world_model = StubWorldModel()
    elif world_model is None:
        from .scenarios import StubWorldModel
        world_model = StubWorldModel()

    if HAS_PLAN and plan is None:
        try:
            plan = Plan()
        except Exception:
            from .scenarios import StubPlan
            plan = StubPlan()
    elif plan is None:
        from .scenarios import StubPlan
        plan = StubPlan()

    if HAS_EVENT_BUS and event_bus is None:
        try:
            event_bus = EventBus()
        except Exception:
            from .scenarios import StubEventBus
            event_bus = StubEventBus()
    elif event_bus is None:
        from .scenarios import StubEventBus
        event_bus = StubEventBus()

    print(f"  [chaos] Running scenario: {name} ...", end=" ")
    sys.stdout.flush()
    t0 = time.time()

    try:
        result = scenario_fn(world_model, plan, event_bus)
        elapsed = time.time() - t0
        result["duration_s"] = round(elapsed, 3)

        if result["passed"]:
            print(f"PASSED ({elapsed:.2f}s)")
            if result.get("recovery_triggered"):
                print(f"          recovery triggered: yes, success: {result['recovery_success']}")
        else:
            print(f"FAILED ({elapsed:.2f}s)")
            print(f"          details: {result.get('details', 'no details')}")

    except Exception as e:
        elapsed = time.time() - t0
        tb = traceback.format_exc()
        result = {
            "scenario": name,
            "passed": False,
            "recovery_triggered": False,
            "recovery_success": False,
            "duration_s": round(elapsed, 3),
            "details": f"Exception: {e}\n{tb}",
        }
        print(f"EXCEPTION ({elapsed:.2f}s): {e}")

    return result


def run_all_chaos_tests(world_model=None, plan=None, event_bus=None, verbose=False):
    """
    Run all registered chaos scenarios.

    Returns:
        dict with keys: scenarios (list of results), summary (pass/fail counts)
    """
    print("\n" + "=" * 60)
    print("CHAOS ENGINEERING TEST SUITE")
    print("=" * 60)

    results = []
    passed = 0
    failed = 0

    for name in SCENARIO_NAMES:
        r = run_chaos_scenario(name, world_model, plan, event_bus)
        results.append(r)
        if r["passed"]:
            passed += 1
        else:
            failed += 1

    # Post-chaos system integrity check
    print("\n  [chaos] Running post-chaos integrity check ...")
    try:
        integrity = verify_system_integrity()
        print(f"  [chaos] Integrity check: {integrity.get('status', 'unknown')}")
        if not integrity.get("all_checks_passed", True):
            print(f"  [chaos] Integrity issues: {integrity.get('issues', [])}")
    except Exception as e:
        print(f"  [chaos] Integrity check exception: {e}")
        integrity = {"status": "error", "error": str(e)}

    print("\n" + "-" * 60)
    print(f"Chaos results: {passed} passed, {failed} failed, {len(results)} total")
    print("-" * 60)

    return {
        "scenarios": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "success_rate": round(passed / len(results) * 100, 1) if results else 0.0,
        },
        "integrity": integrity,
    }


def run_scenario_simulated(name):
    """Run a scenario as a standalone simulation (no core dependencies)."""
    from .scenarios import StubWorldModel, StubPlan, StubEventBus
    return run_chaos_scenario(name, StubWorldModel(), StubPlan(), StubEventBus())


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for name in sys.argv[1:]:
            print(json.dumps(run_scenario_simulated(name), indent=2))
    else:
        results = run_all_chaos_tests()
        print(f"\nSummary: {results['summary']}")
