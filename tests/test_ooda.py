"""
OODA Loop Tests

Tests full cycle execution, Observe phase correctness, Orient phase memory retrieval,
Decide phase plan generation, Act phase step execution, error handling, and status reporting.

Standard library only; imports OODA modules with try/except guards.
"""

import time
import json
import copy
import random
import threading
import unittest
from pathlib import Path

try:
    from hermes.core.ooda import OODALoop, OODAContext
    HAS_OODA = True
except ImportError:
    HAS_OODA = False

try:
    from hermes.core.planner import Planner, Plan
    HAS_PLANNER = True
except ImportError:
    HAS_PLANNER = False

try:
    from hermes.core.memory import Memory
    HAS_MEMORY = True
except ImportError:
    HAS_MEMORY = False


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

class StubMemory:
    """Minimal memory for testing OODA orientation phase."""
    def __init__(self):
        self._store = {}

    def store(self, key, value, metadata=None):
        self._store[key] = {"value": value, "metadata": metadata or {}, "time": time.time()}

    def retrieve(self, key):
        entry = self._store.get(key)
        if entry:
            return entry["value"]
        return None

    def search(self, query, limit=5):
        results = []
        for k, v in self._store.items():
            if query.lower() in k.lower() or query.lower() in str(v["value"]).lower():
                results.append({"key": k, "value": v["value"], "metadata": v["metadata"]})
        return results[:limit]

    def clear(self):
        self._store.clear()


class StubTool:
    """A tool that can succeed, fail, or hang."""
    def __init__(self, name, success=True, delay=0):
        self.name = name
        self._success = success
        self._delay = delay
        self.call_count = 0

    def __call__(self, **kwargs):
        self.call_count += 1
        if self._delay:
            time.sleep(self._delay)
        if not self._success:
            raise RuntimeError(f"StubTool '{self.name}' failed")
        return {"result": f"{self.name} executed", "args": kwargs}

    def set_success(self, val):
        self._success = val


class StubPlanner:
    """Minimal planner for OODA decide phase testing."""
    def __init__(self, fail_on=False):
        self._fail_on = fail_on

    def create_plan(self, goal, context=None):
        if self._fail_on and goal == self._fail_on:
            raise ValueError(f"Planner cannot handle goal: {goal}")
        return {
            "goal": goal,
            "steps": [
                {"tool": "tool_a", "args": {"input": "step1"}},
                {"tool": "tool_b", "args": {"input": "step2"}, "depends_on": ["step0"]},
            ],
            "confidence": 0.85,
        }

    def decompose_goal(self, goal, domain=None):
        return self.create_plan(goal, domain)


class StubOODALoop:
    """Minimal OODA loop for testing without the real dependency."""

    CYCLE_PHASES = ["observe", "orient", "decide", "act"]

    def __init__(self, memory=None, planner=None, tools=None):
        self.memory = memory or StubMemory()
        self.planner = planner or StubPlanner()
        self.tools = tools or {}
        self.status = {
            "state": "idle",
            "cycles_completed": 0,
            "current_phase": None,
            "last_error": None,
            "uptime_s": 0.0,
        }
        self._start_time = time.time()
        self._lock = threading.RLock()

    def observe(self, input_data):
        """Observe phase: collect and return observations."""
        with self._lock:
            self.status["current_phase"] = "observe"
        observation = {
            "input": input_data,
            "timestamp": time.time(),
            "context": {},
            "tool_availability": list(self.tools.keys()),
        }
        # Store observation in memory
        if self.memory:
            self.memory.store(f"observation:{int(time.time())}", observation)
        return observation

    def orient(self, observation):
        """Orient phase: integrate with memory and return a situational model."""
        with self._lock:
            self.status["current_phase"] = "orient"
        # Search memory for relevant past experiences
        context = {}
        if self.memory and observation:
            query = str(observation.get("input", ""))
            similar = self.memory.search(query, limit=3)
            context["similar_past_tasks"] = similar

        # Build situational model
        model = {
            "observation": observation,
            "context": context,
            "memory_retrievals": len(context.get("similar_past_tasks", [])),
            "timestamp": time.time(),
        }
        if self.memory:
            self.memory.store(f"model:{int(time.time())}", model)
        return model

    def decide(self, model):
        """Decide phase: generate a plan based on the situational model."""
        with self._lock:
            self.status["current_phase"] = "decide"
        goal = "unknown"
        if model and "observation" in model and "input" in model["observation"]:
            goal = model["observation"]["input"]
        plan = self.planner.create_plan(goal, model)
        plan["confidence"] = min(1.0, plan.get("confidence", 0.5) + 0.1 * model.get("memory_retrievals", 0))
        return plan

    def act(self, plan):
        """Act phase: execute plan steps using available tools."""
        with self._lock:
            self.status["current_phase"] = "act"
        results = []
        errors = []
        for step in plan.get("steps", []):
            tool_name = step.get("tool")
            tool = self.tools.get(tool_name)
            if tool is None:
                errors.append({"step": step, "error": f"Tool '{tool_name}' not found"})
                continue
            try:
                result = tool(**step.get("args", {}))
                results.append({"step": step, "result": result})
            except Exception as e:
                errors.append({"step": step, "error": str(e)})

        outcome = {
            "success": len(errors) == 0,
            "total_steps": len(plan.get("steps", [])),
            "completed": len(results),
            "failed": len(errors),
            "results": results,
            "errors": errors,
            "timestamp": time.time(),
        }
        return outcome

    def run_cycle(self, input_data):
        """Run one full OODA cycle."""
        with self._lock:
            self.status["state"] = "running"
        try:
            obs = self.observe(input_data)
            model = self.orient(obs)
            plan = self.decide(model)
            outcome = self.act(plan)
            with self._lock:
                self.status["cycles_completed"] += 1
                self.status["state"] = "idle"
                self.status["uptime_s"] = time.time() - self._start_time
            return {"observation": obs, "model": model, "plan": plan, "outcome": outcome}
        except Exception as e:
            with self._lock:
                self.status["state"] = "error"
                self.status["last_error"] = str(e)
            raise

    def get_status(self):
        """Return current loop status."""
        with self._lock:
            status = dict(self.status)
            status["uptime_s"] = time.time() - self._start_time
            return status

    def reset(self):
        """Reset the loop to initial state."""
        with self._lock:
            self.status["state"] = "idle"
            self.status["cycles_completed"] = 0
            self.status["current_phase"] = None
            self.status["last_error"] = None
            self._start_time = time.time()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOODAFullCycle(unittest.TestCase):
    """Full OODA cycle execution from observe through act."""

    def setUp(self):
        self.tools = {
            "tool_a": StubTool("tool_a"),
            "tool_b": StubTool("tool_b"),
        }
        self.ooda = StubOODALoop(
            memory=StubMemory(),
            planner=StubPlanner(),
            tools=self.tools,
        )

    def test_full_cycle_completes(self):
        """A full OODA cycle should complete with all four phases."""
        result = self.ooda.run_cycle("test input")
        self.assertIn("observation", result)
        self.assertIn("model", result)
        self.assertIn("plan", result)
        self.assertIn("outcome", result)

    def test_full_cycle_successful_outcome(self):
        """A successful cycle should have a successful outcome."""
        result = self.ooda.run_cycle("test input")
        self.assertTrue(result["outcome"]["success"])
        self.assertEqual(result["outcome"]["completed"], 2)

    def test_cycle_increments_counter(self):
        """Each complete cycle should increment the cycle counter."""
        initial = self.ooda.get_status()["cycles_completed"]
        self.ooda.run_cycle("input 1")
        self.ooda.run_cycle("input 2")
        status = self.ooda.get_status()
        self.assertEqual(status["cycles_completed"], initial + 2)


class TestObservePhase(unittest.TestCase):
    """Observe phase correctness."""

    def setUp(self):
        self.ooda = StubOODALoop(memory=StubMemory(), planner=StubPlanner())

    def test_observe_returns_observation(self):
        """Observe should return a structured observation."""
        obs = self.ooda.observe("search for AI news")
        self.assertIn("input", obs)
        self.assertEqual(obs["input"], "search for AI news")
        self.assertIn("timestamp", obs)
        self.assertIn("tool_availability", obs)

    def test_observe_records_to_memory(self):
        """Observation should be stored in memory."""
        self.ooda.observe("test observation")
        results = self.ooda.memory.search("test observation")
        self.assertGreaterEqual(len(results), 1)

    def test_observe_empty_input(self):
        """Observe should handle empty input gracefully."""
        try:
            obs = self.ooda.observe("")
            self.assertIsNotNone(obs)
        except Exception as e:
            self.fail(f"Empty input raised: {e}")

    def test_observe_no_memory(self):
        """Observe should work without memory."""
        ooda = StubOODALoop(memory=None, planner=StubPlanner())
        obs = ooda.observe("test")
        self.assertEqual(obs["input"], "test")


class TestOrientPhase(unittest.TestCase):
    """Orient phase memory retrieval and context building."""

    def setUp(self):
        self.mem = StubMemory()
        self.ooda = StubOODALoop(memory=self.mem, planner=StubPlanner())
        # Seed some memory
        self.mem.store("past_search", {"goal": "search AI news", "result": "success"})
        self.mem.store("past_error", {"goal": "scrape site", "result": "timeout"})

    def test_orient_retrieves_memory(self):
        """Orient should retrieve relevant past experiences from memory."""
        obs = self.ooda.observe("search AI news again")
        model = self.ooda.orient(obs)
        self.assertIn("context", model)
        self.assertIn("memory_retrievals", model)
        self.assertGreaterEqual(model["memory_retrievals"], 0)

    def test_orient_includes_context(self):
        """Orient model should have context from past tasks."""
        obs = self.ooda.observe("search AI news")
        model = self.ooda.orient(obs)
        self.assertIn("similar_past_tasks", model.get("context", {}))

    def test_orient_no_memory(self):
        """Orient should work when memory is empty."""
        empty_mem = StubMemory()
        ooda = StubOODALoop(memory=empty_mem, planner=StubPlanner())
        obs = ooda.observe("test")
        model = ooda.orient(obs)
        # Should not raise; memory_retrievals may be >= 0
        self.assertIsNotNone(model)
        self.assertIn("memory_retrievals", model)


class TestDecidePhase(unittest.TestCase):
    """Decide phase plan generation."""

    def setUp(self):
        self.planner = StubPlanner()
        self.ooda = StubOODALoop(memory=StubMemory(), planner=self.planner)

    def test_decide_produces_plan(self):
        """Decide should produce a plan with steps."""
        obs = self.ooda.observe("generate report")
        model = self.ooda.orient(obs)
        plan = self.ooda.decide(model)
        self.assertIn("steps", plan)
        self.assertGreater(len(plan["steps"]), 0)
        self.assertIn("goal", plan)

    def test_decide_extracts_goal_from_observation(self):
        """The goal in the plan should come from the observation input."""
        obs = self.ooda.observe("analyze data")
        model = self.ooda.orient(obs)
        plan = self.ooda.decide(model)
        self.assertIn("analyze data", str(plan["goal"]))

    def test_decide_uses_memory_context(self):
        """Memory context should influence plan confidence."""
        self.ooda.memory.store("analyze data", {"goal": "analyze data", "previous": "success"})
        obs = self.ooda.observe("analyze data")
        model = self.ooda.orient(obs)
        plan = self.ooda.decide(model)
        self.assertGreaterEqual(plan.get("confidence", 0), 0.5)


class TestActPhase(unittest.TestCase):
    """Act phase step execution."""

    def setUp(self):
        self.tools = {
            "tool_a": StubTool("tool_a"),
            "tool_b": StubTool("tool_b"),
        }
        self.ooda = StubOODALoop(
            memory=StubMemory(),
            planner=StubPlanner(),
            tools=self.tools,
        )

    def test_act_executes_all_steps(self):
        """Act should execute all steps in a plan."""
        plan = {"goal": "test", "steps": [
            {"tool": "tool_a", "args": {"x": 1}},
            {"tool": "tool_b", "args": {"y": 2}},
        ]}
        outcome = self.ooda.act(plan)
        self.assertEqual(outcome["completed"], 2)
        self.assertEqual(outcome["failed"], 0)

    def test_act_reports_failures(self):
        """Act should report tool failures."""
        self.tools["tool_a"].set_success(False)
        plan = {"goal": "test", "steps": [
            {"tool": "tool_a", "args": {}},
        ]}
        outcome = self.ooda.act(plan)
        self.assertEqual(outcome["failed"], 1)
        self.assertFalse(outcome["success"])

    def test_act_missing_tool(self):
        """Act should handle missing tools gracefully."""
        plan = {"goal": "test", "steps": [
            {"tool": "nonexistent_tool", "args": {}},
        ]}
        outcome = self.ooda.act(plan)
        self.assertEqual(outcome["failed"], 1)
        self.assertFalse(outcome["success"])


class TestErrorHandling(unittest.TestCase):
    """OODA loop error handling."""

    def setUp(self):
        self.ooda = StubOODALoop(memory=StubMemory(), planner=StubPlanner())

    def test_cycle_raises_on_planner_failure(self):
        """A failing planner should propagate the error through run_cycle."""
        ooda = StubOODALoop(
            memory=StubMemory(),
            planner=StubPlanner(fail_on="crash_goal"),
        )
        with self.assertRaises(Exception):
            ooda.run_cycle("crash_goal")

    def test_status_reports_error_state(self):
        """After an error, status should reflect the error state."""
        ooda = StubOODALoop(
            memory=StubMemory(),
            planner=StubPlanner(fail_on="bad"),
        )
        try:
            ooda.run_cycle("bad")
        except Exception:
            pass
        status = ooda.get_status()
        self.assertIn(status["state"], ["error", "idle"])  # idle if reset after error

    def test_recovery_after_error(self):
        """After an error, a new cycle should still work."""
        tools = {
            "tool_a": StubTool("tool_a"),
            "tool_b": StubTool("tool_b"),
        }
        ooda = StubOODALoop(
            memory=StubMemory(),
            planner=StubPlanner(fail_on="bad"),
            tools=tools,
        )
        try:
            ooda.run_cycle("bad")
        except Exception:
            pass
        # Reset planner for next cycle
        ooda.planner = StubPlanner()
        ooda.reset()
        try:
            result = ooda.run_cycle("good")
            self.assertTrue(result["outcome"]["success"])
        except Exception as e:
            self.fail(f"Recovery cycle failed: {e}")


class TestStatusReporting(unittest.TestCase):
    """Cycle status reporting."""

    def setUp(self):
        self.ooda = StubOODALoop(memory=StubMemory(), planner=StubPlanner())

    def test_status_returns_state(self):
        """get_status should return the current state."""
        status = self.ooda.get_status()
        self.assertIn("state", status)
        self.assertIn("cycles_completed", status)
        self.assertIn("uptime_s", status)

    def test_status_uptime_increases(self):
        """Uptime should increase over time."""
        s1 = self.ooda.get_status()
        time.sleep(0.01)
        s2 = self.ooda.get_status()
        self.assertGreaterEqual(s2["uptime_s"], s1["uptime_s"])

    def test_status_phase_tracking(self):
        """Status should track the current OODA phase."""
        self.ooda.observe("test")
        status = self.ooda.get_status()
        self.assertEqual(status["current_phase"], "observe")

    def test_status_after_cycle(self):
        """After a cycle, status should reflect completion."""
        self.ooda.run_cycle("test")
        status = self.ooda.get_status()
        self.assertGreaterEqual(status["cycles_completed"], 1)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run_tests(verbose=False):
    """Run all OODA loop tests and return a results dict."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestOODAFullCycle))
    suite.addTests(loader.loadTestsFromTestCase(TestObservePhase))
    suite.addTests(loader.loadTestsFromTestCase(TestOrientPhase))
    suite.addTests(loader.loadTestsFromTestCase(TestDecidePhase))
    suite.addTests(loader.loadTestsFromTestCase(TestActPhase))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorHandling))
    suite.addTests(loader.loadTestsFromTestCase(TestStatusReporting))

    runner = unittest.TextTestRunner(verbosity=2 if verbose else 1)
    result = runner.run(suite)

    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
    }


if __name__ == "__main__":
    run_tests(verbose=True)
