#!/usr/bin/env python3
"""LLM integration example for hermes-cognitive.

Demonstrates how to use the Planner with LLM integration
for task decomposition and plan generation.

Prerequisites:
    - Set OPENAI_API_KEY environment variable
    - Or configure a compatible API endpoint
"""

import os
from hermes_core.core import (
    core_initialize,
    get_kernel_singleton,
)


def main():
    print("=" * 60)
    print("hermes-cognitive LLM Integration Example")
    print("=" * 60)

    # Check for API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("\n⚠ Warning: OPENAI_API_KEY not set.")
        print("  Set it with: export OPENAI_API_KEY='your-key'")
        print("  This example will use mock data instead.\n")

    # ----------------------------------------------------------------
    # 1. Initialize core system
    # ----------------------------------------------------------------
    print("\n[1] Initializing core system...")
    core_initialize()

    # ----------------------------------------------------------------
    # 2. Get kernel and planner
    # ----------------------------------------------------------------
    print("\n[2] Getting kernel and planner...")
    kernel = get_kernel_singleton()
    planner = kernel.planner

    # ----------------------------------------------------------------
    # 3. Generate a plan
    # ----------------------------------------------------------------
    print("\n[3] Generating execution plan...")

    goal = "Analyze the latest developments in quantum computing"

    try:
        plan = planner.plan(
            goal=goal,
            constraints={
                "max_steps": 5,
                "time_limit": 300,
                "domain": "physics",
            }
        )

        print(f"    Goal: {goal}")
        print(f"    Plan generated with {len(plan.get('steps', []))} steps:")

        for i, step in enumerate(plan.get("steps", []), 1):
            print(f"      {i}. {step.get('description', 'N/A')}")
            print(f"         Tool: {step.get('tool', 'N/A')}")
            print(f"         Risk: {step.get('risk_level', 'N/A')}")

    except Exception as e:
        print(f"    Error: {e}")
        print("    (This is expected if no LLM API is configured)")

    # ----------------------------------------------------------------
    # 4. Execute OODA cycle
    # ----------------------------------------------------------------
    print("\n[4] Executing OODA cycle...")

    try:
        result = kernel.execute_ooda_cycle(
            observation="User requests quantum computing research",
            context={
                "domain": "physics",
                "task_type": "research",
                "user_intent": "literature_review",
            }
        )

        print(f"    Observation: quantum computing research request")
        print(f"    Decision: {result.get('decision', 'N/A')}")
        print(f"    Action: {result.get('action', 'N/A')}")

    except Exception as e:
        print(f"    Error: {e}")
        print("    (This is expected if no LLM API is configured)")

    # ----------------------------------------------------------------
    # 5. Memory and learning
    # ----------------------------------------------------------------
    print("\n[5] Storing experience...")

    from hermes_core.core import get_memory_manager_singleton

    memory = get_memory_manager_singleton()

    # Store the research experience
    memory.store(
        key="quantum_research_2026",
        value={
            "goal": goal,
            "domain": "physics",
            "outcome": "completed",
            "learnings": [
                "Quantum supremacy achieved in 2025",
                "Error correction improved significantly",
                "New algorithms for quantum machine learning",
            ],
        },
        category="episodic"
    )
    print("    Experience stored in episodic memory")

    # ----------------------------------------------------------------
    # Done
    # ----------------------------------------------------------------
    print("\n" + "=" * 60)
    print("LLM integration example completed!")
    print("=" * 60)
    print("\nNote: Full functionality requires a configured LLM API.")
    print("Set OPENAI_API_KEY or configure a compatible endpoint.")


if __name__ == "__main__":
    main()
