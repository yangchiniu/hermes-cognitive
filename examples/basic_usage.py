#!/usr/bin/env python3
"""Basic usage example for hermes-cognitive.

Demonstrates core initialization, health check, policy evaluation,
and memory operations.
"""

from hermes_core.core import (
    core_initialize,
    core_health_check,
    core_status,
    get_kernel_singleton,
    get_policy_engine,
    get_memory_manager_singleton,
    get_telemetry_singleton,
    get_drift_analyzer,
)


def main():
    print("=" * 60)
    print("hermes-cognitive Basic Usage Example")
    print("=" * 60)

    # ----------------------------------------------------------------
    # 1. Initialize the core system
    # ----------------------------------------------------------------
    print("\n[1] Initializing core system...")
    init_result = core_initialize()
    print(f"    Result: {init_result}")

    # ----------------------------------------------------------------
    # 2. Health check
    # ----------------------------------------------------------------
    print("\n[2] Running health check...")
    health = core_health_check()
    print(f"    Status: {health}")

    # ----------------------------------------------------------------
    # 3. System status
    # ----------------------------------------------------------------
    print("\n[3] Getting system status...")
    status = core_status()
    for key, value in status.items():
        print(f"    {key}: {value}")

    # ----------------------------------------------------------------
    # 4. Policy engine
    # ----------------------------------------------------------------
    print("\n[4] Policy Engine...")
    policy = get_policy_engine()

    # Evaluate a safe action
    result = policy.evaluate_action(
        action_type="terminal_exec",
        context={"command": "ls -la"}
    )
    print(f"    Safe action (ls -la): {result}")

    # Evaluate a dangerous action
    result = policy.evaluate_action(
        action_type="destructive_shell",
        context={"command": "rm -rf /"}
    )
    print(f"    Dangerous action (rm -rf /): {result}")

    # ----------------------------------------------------------------
    # 5. Memory operations
    # ----------------------------------------------------------------
    print("\n[5] Memory Manager...")
    memory = get_memory_manager_singleton()

    # Store a memory
    memory.store(
        key="demo_key",
        value="Hello from hermes-cognitive!",
        category="working"
    )
    print("    Stored: demo_key = 'Hello from hermes-cognitive!'")

    # Retrieve the memory
    value = memory.retrieve("demo_key")
    print(f"    Retrieved: {value}")

    # ----------------------------------------------------------------
    # 6. Telemetry
    # ----------------------------------------------------------------
    print("\n[6] Telemetry...")
    telemetry = get_telemetry_singleton()
    report = telemetry.get_summary()
    print(f"    Summary: {report}")

    # ----------------------------------------------------------------
    # 7. Drift Analyzer
    # ----------------------------------------------------------------
    print("\n[7] Drift Analyzer...")
    drift = get_drift_analyzer()
    drift_status = drift.get_status()
    print(f"    Status: {drift_status}")

    # ----------------------------------------------------------------
    # Done
    # ----------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Example completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
