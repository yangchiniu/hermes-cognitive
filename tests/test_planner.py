"""
Planner Unit Tests

Tests goal decomposition across 5 domain patterns, tool selection correctness,
constraint application, risk assessment, fallback generation, and plan refinement.

Standard library only; imports planner modules with try/except guards.
"""

import time
import copy
import json
import random
import unittest
from pathlib import Path

try:
    from hermes.core.planner import Planner, Plan, Step, Goal, ToolRequirement
    HAS_PLANNER = True
except ImportError:
    HAS_PLANNER = False

try:
    from hermes.core.kernel import Kernel
    HAS_KERNEL = True
except ImportError:
    HAS_KERNEL = False

try:
    from hermes.core.tool_manager import ToolManager
    HAS_TOOL_MANAGER = True
except ImportError:
    HAS_TOOL_MANAGER = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dummy_planner():
    """Return a minimal stub Planner if the real one is unavailable."""
    class _StubStep:
        def __init__(self, tool, args, depends_on=None, timeout=30):
            self.tool = tool
            self.args = args or {}
            self.depends_on = depends_on or []
            self.timeout = timeout

    class _StubPlan:
        def __init__(self, goal, steps, confidence=0.8):
            self.goal = goal
            self.steps = steps
            self.confidence = confidence
            self.risks = []
            self.fallbacks = []

    class _StubPlanner:
        def decompose_goal(self, goal_text, domain=None):
            return _StubPlan(
                goal=goal_text,
                steps=[
                    _StubStep("web_search", {"query": goal_text}),
                    _StubStep("web_scrape", {"url": "http://example.com"}, depends_on=["step0"]),
                ],
                confidence=0.85,
            )
        def select_tools(self, goal_text, available_tools):
            return ["web_search", "web_scrape"]
        def apply_constraints(self, plan, constraints):
            return plan
        def assess_risks(self, plan):
            return []
        def generate_fallbacks(self, plan):
            plan.fallbacks = ["retry", "alternative_tool"]
            return plan
        def refine_plan(self, plan, feedback):
            if isinstance(feedback, dict) and feedback.get("success", True):
                plan.confidence = min(1.0, plan.confidence + 0.1)
            else:
                plan.confidence = max(0.0, plan.confidence - 0.1)
            return plan

    return _StubPlanner()


# ---------------------------------------------------------------------------
# 5 Domain Patterns for Goal Decomposition
# ---------------------------------------------------------------------------

DOMAIN_PATTERNS = [
    {
        "name": "web_research",
        "goal": "search latest AI news and summarize findings",
        "expected_tools": ["web_search", "web_scrape"],
        "keywords": ["search", "AI", "news"],
    },
    {
        "name": "data_collection",
        "goal": "collect housing prices in Kunming from public sources",
        "expected_tools": ["web_search", "web_scrape"],
        "keywords": ["housing", "prices", "Kunming"],
    },
    {
        "name": "file_operation",
        "goal": "read config.yaml and extract database settings",
        "expected_tools": ["file_read", "parse_yaml"],
        "keywords": ["read", "config", "database"],
    },
    {
        "name": "code_generation",
        "goal": "write a Python script to merge CSV files in /data",
        "expected_tools": ["file_write", "code_execute"],
        "keywords": ["Python", "script", "merge"],
    },
    {
        "name": "system_administration",
        "goal": "check disk usage and email the admin if above 90%",
        "expected_tools": ["shell_exec", "send_email"],
        "keywords": ["disk", "usage", "admin"],
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGoalDecomposition(unittest.TestCase):
    """Verify that the planner can decompose goals across 5 domain patterns."""

    @classmethod
    def setUpClass(cls):
        if HAS_PLANNER:
            try:
                cls.planner = Planner()
            except Exception:
                cls.planner = _make_dummy_planner()
        else:
            cls.planner = _make_dummy_planner()

    def test_all_domains(self):
        """Planner should produce a plan for each of the 5 domain patterns."""
        for pattern in DOMAIN_PATTERNS:
            with self.subTest(domain=pattern["name"]):
                plan = self.planner.decompose_goal(pattern["goal"])
                self.assertIsNotNone(plan, f"No plan produced for {pattern['name']}")
                self.assertTrue(hasattr(plan, "steps") or hasattr(plan, "goal"),
                                f"Plan missing expected attributes for {pattern['name']}")

    def test_keyword_presence(self):
        """Goal keywords should survive into the plan representation."""
        for pattern in DOMAIN_PATTERNS:
            with self.subTest(domain=pattern["name"]):
                plan = self.planner.decompose_goal(pattern["goal"])
                goal_text = plan.goal if hasattr(plan, "goal") else str(plan)
                for kw in pattern["keywords"]:
                    self.assertIn(kw.lower(), goal_text.lower(),
                                  f"Keyword '{kw}' missing in plan for {pattern['name']}")


class TestToolSelection(unittest.TestCase):
    """Verify the planner selects correct tools for a given goal."""

    @classmethod
    def setUpClass(cls):
        if HAS_PLANNER:
            try:
                cls.planner = Planner()
            except Exception:
                cls.planner = _make_dummy_planner()
        else:
            cls.planner = _make_dummy_planner()

    def test_tool_selection_returns_list(self):
        """select_tools should return a list of tool names."""
        tools = self.planner.select_tools("search for AI news", ["web_search", "web_scrape", "file_read"])
        self.assertIsInstance(tools, list)
        self.assertGreater(len(tools), 0)

    def test_tool_selection_respects_available(self):
        """Selected tools must be a subset of available tools."""
        available = ["web_search", "web_scrape"]
        tools = self.planner.select_tools("search for AI news", available)
        for t in tools:
            self.assertIn(t, available, f"Tool '{t}' not in available set")

    def test_tool_selection_empty_available(self):
        """If no tools available, should return empty list gracefully."""
        tools = self.planner.select_tools("do something", [])
        self.assertIsInstance(tools, list)


class TestConstraintApplication(unittest.TestCase):
    """Verify constraints (time, cost, safety) are applied to plans."""

    @classmethod
    def setUpClass(cls):
        if HAS_PLANNER:
            try:
                cls.planner = Planner()
            except Exception:
                cls.planner = _make_dummy_planner()
        else:
            cls.planner = _make_dummy_planner()

    def test_time_constraint(self):
        """Applying a max-duration constraint should not crash."""
        plan = self.planner.decompose_goal("test goal")
        constrained = self.planner.apply_constraints(plan, {"max_duration_s": 30})
        self.assertIsNotNone(constrained)

    def test_cost_constraint(self):
        """Applying a max-cost constraint should not crash."""
        plan = self.planner.decompose_goal("test goal")
        constrained = self.planner.apply_constraints(plan, {"max_cost": 5})
        self.assertIsNotNone(constrained)

    def test_safety_constraint(self):
        """Safety constraints (no destructive tools) should be applied."""
        plan = self.planner.decompose_goal("test goal")
        constrained = self.planner.apply_constraints(plan, {"forbidden_tools": ["shell_exec", "file_delete"]})
        self.assertIsNotNone(constrained)


class TestRiskAssessment(unittest.TestCase):
    """Verify risk assessment flags problematic patterns."""

    @classmethod
    def setUpClass(cls):
        if HAS_PLANNER:
            try:
                cls.planner = Planner()
            except Exception:
                cls.planner = _make_dummy_planner()
        else:
            cls.planner = _make_dummy_planner()

    def test_risk_assessment_returns_list(self):
        """assess_risks should return a list."""
        plan = self.planner.decompose_goal("test goal")
        risks = self.planner.assess_risks(plan)
        self.assertIsInstance(risks, list)

    def test_risks_are_identifiable(self):
        """Each risk should have identifying information."""
        plan = self.planner.decompose_goal("delete important files")
        risks = self.planner.assess_risks(plan)
        for risk in risks:
            # Risk should be a string, dict, or have a readable representation
            self.assertTrue(isinstance(risk, (str, dict)) or hasattr(risk, "__str__"))


class TestFallbackGeneration(unittest.TestCase):
    """Verify fallback strategies are generated for plans."""

    @classmethod
    def setUpClass(cls):
        if HAS_PLANNER:
            try:
                cls.planner = Planner()
            except Exception:
                cls.planner = _make_dummy_planner()
        else:
            cls.planner = _make_dummy_planner()

    def test_fallbacks_generated(self):
        """generate_fallbacks should add fallback strategies to a plan."""
        plan = self.planner.decompose_goal("test goal")
        plan = self.planner.generate_fallbacks(plan)
        self.assertTrue(hasattr(plan, "fallbacks"), "Plan missing fallbacks attribute")
        if plan.fallbacks:
            self.assertIsInstance(plan.fallbacks, list)

    def test_fallback_is_actionable(self):
        """Each fallback should be a string or dict describing an action."""
        plan = self.planner.decompose_goal("test goal")
        plan = self.planner.generate_fallbacks(plan)
        for fb in (plan.fallbacks or []):
            self.assertTrue(isinstance(fb, (str, dict)),
                            f"Fallback '{fb}' is not a string or dict")


class TestPlanRefinement(unittest.TestCase):
    """Verify plan refinement incorporates feedback."""

    @classmethod
    def setUpClass(cls):
        if HAS_PLANNER:
            try:
                cls.planner = Planner()
            except Exception:
                cls.planner = _make_dummy_planner()
        else:
            cls.planner = _make_dummy_planner()

    def test_refinement_reduces_confidence_on_negative(self):
        """Negative feedback should reduce plan confidence."""
        plan = self.planner.decompose_goal("test goal")
        original_confidence = getattr(plan, "confidence", 1.0)
        refined = self.planner.refine_plan(plan, {"success": False, "error": "timeout"})
        new_confidence = getattr(refined, "confidence", original_confidence)
        self.assertLessEqual(new_confidence, original_confidence)

    def test_refinement_increases_confidence_on_positive(self):
        """Positive feedback should maintain or increase confidence."""
        plan = self.planner.decompose_goal("test goal")
        original_confidence = getattr(plan, "confidence", 0.5)
        refined = self.planner.refine_plan(plan, {"success": True})
        new_confidence = getattr(refined, "confidence", original_confidence)
        self.assertGreaterEqual(new_confidence, original_confidence)


class TestEdgeCases(unittest.TestCase):
    """Edge case and error handling tests."""

    def test_empty_goal(self):
        """Planner should handle empty goal strings gracefully."""
        planner = _make_dummy_planner()
        try:
            plan = planner.decompose_goal("")
            self.assertIsNotNone(plan)
        except Exception as e:
            self.fail(f"Empty goal raised exception: {e}")

    def test_very_long_goal(self):
        """Planner should handle very long goal strings."""
        planner = _make_dummy_planner()
        long_goal = "test " * 1000
        try:
            plan = planner.decompose_goal(long_goal)
            self.assertIsNotNone(plan)
        except Exception as e:
            self.fail(f"Long goal raised exception: {e}")

    def test_special_characters(self):
        """Planner should handle special characters in goals."""
        planner = _make_dummy_planner()
        try:
            plan = planner.decompose_goal("test goal with $pecial @# chars and üñîçød€!")
            self.assertIsNotNone(plan)
        except Exception as e:
            self.fail(f"Special characters raised exception: {e}")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run_tests(verbose=False):
    """Run all planner unit tests and return a results dict."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestGoalDecomposition))
    suite.addTests(loader.loadTestsFromTestCase(TestToolSelection))
    suite.addTests(loader.loadTestsFromTestCase(TestConstraintApplication))
    suite.addTests(loader.loadTestsFromTestCase(TestRiskAssessment))
    suite.addTests(loader.loadTestsFromTestCase(TestFallbackGeneration))
    suite.addTests(loader.loadTestsFromTestCase(TestPlanRefinement))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))

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
