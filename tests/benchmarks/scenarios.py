"""
Benchmark Scenarios

Defines benchmark scenarios for measuring Hermes Core agent performance.
Each scenario includes a goal, expected tool set, and duration constraints.

Standard library only.
"""

# ---------------------------------------------------------------------------
# Benchmark scenario definitions
# ---------------------------------------------------------------------------

BENCHMARK_SCENARIOS = {
    "web_search": {
        "goal": "search latest AI news",
        "expected_tools": ["web_search", "web_scrape"],
        "max_duration_s": 60,
        "min_success_rate": 0.8,
        "category": "web",
        "description": "Standard web search scenario.",
    },
    "data_collection": {
        "goal": "collect housing prices in Kunming",
        "expected_tools": ["web_search", "web_scrape"],
        "max_duration_s": 300,
        "min_success_rate": 0.7,
        "category": "data",
        "description": "Multi-page data collection.",
    },
    "file_operation": {
        "goal": "read and summarize a file",
        "expected_tools": ["file_read"],
        "max_duration_s": 30,
        "min_success_rate": 0.9,
        "category": "file",
        "description": "Simple file read and summary.",
    },
    "error_recovery": {
        "goal": "scrape a page that fails, verify fallback",
        "expected_tools": ["web_scrape", "web_search"],
        "max_duration_s": 60,
        "min_success_rate": 0.6,
        "category": "resilience",
        "description": "Tests fallback on simulated failure.",
        "simulate_failure": True,
    },
}

# Derived convenience mappings
SCENARIO_NAMES = list(BENCHMARK_SCENARIOS.keys())
SCENARIO_COUNT = len(BENCHMARK_SCENARIOS)


def get_scenario(name):
    """Get a benchmark scenario by name, or None if not found."""
    return BENCHMARK_SCENARIOS.get(name)


def list_scenarios(category=None):
    """
    List all benchmarks, optionally filtered by category.

    Args:
        category: Filter by category name (web, data, file, resilience).

    Returns:
        list of (name, scenario) tuples.
    """
    if category:
        return [(n, s) for n, s in BENCHMARK_SCENARIOS.items() if s.get("category") == category]
    return list(BENCHMARK_SCENARIOS.items())


def validate_scenario(name):
    """
    Validate that a scenario definition is well-formed.

    Returns (is_valid: bool, errors: list[str]).
    """
    scenario = BENCHMARK_SCENARIOS.get(name)
    if not scenario:
        return False, [f"Unknown scenario: {name}"]

    errors = []
    if "goal" not in scenario:
        errors.append("Missing 'goal'")
    if "expected_tools" not in scenario:
        errors.append("Missing 'expected_tools'")
    if "max_duration_s" not in scenario:
        errors.append("Missing 'max_duration_s'")
    else:
        try:
            if float(scenario["max_duration_s"]) <= 0:
                errors.append("max_duration_s must be positive")
        except (TypeError, ValueError):
            errors.append("max_duration_s must be numeric")

    return len(errors) == 0, errors
