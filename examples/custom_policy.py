#!/usr/bin/env python3
"""Custom policy example for hermes-cognitive.

Demonstrates how to create and use custom security policies.
"""

from hermes_core.core import get_policy_engine


def main():
    print("=" * 60)
    print("hermes-cognitive Custom Policy Example")
    print("=" * 60)

    # ----------------------------------------------------------------
    # 1. Get the policy engine
    # ----------------------------------------------------------------
    policy = get_policy_engine()

    # ----------------------------------------------------------------
    # 2. Evaluate various actions
    # ----------------------------------------------------------------
    print("\n[1] Evaluating actions...")

    test_cases = [
        {
            "name": "Safe terminal command",
            "action_type": "terminal_exec",
            "context": {"command": "python --version"},
        },
        {
            "name": "List directory",
            "action_type": "terminal_exec",
            "context": {"command": "ls -la /tmp"},
        },
        {
            "name": "Dangerous command (rm -rf /)",
            "action_type": "terminal_exec",
            "context": {"command": "rm -rf /"},
        },
        {
            "name": "Format filesystem (mkfs)",
            "action_type": "terminal_exec",
            "context": {"command": "mkfs.ext4 /dev/sda"},
        },
        {
            "name": "Destructive shell operation",
            "action_type": "destructive_shell",
            "context": {"command": "dd if=/dev/zero of=/dev/sda"},
        },
        {
            "name": "CAPTCHA bypass attempt",
            "action_type": "captcha_bypass",
            "context": {"url": "https://example.com"},
        },
        {
            "name": "Normal web request",
            "action_type": "browser_interact",
            "context": {"url": "https://example.com", "action": "navigate"},
        },
        {
            "name": "Bank website access",
            "action_type": "browser_interact",
            "context": {"url": "https://www.bank.com", "action": "navigate"},
        },
    ]

    for tc in test_cases:
        result = policy.evaluate_action(
            action_type=tc["action_type"],
            context=tc["context"]
        )
        status = "✓ ALLOWED" if result.get("allowed") else "✗ DENIED"
        print(f"    {status} | {tc['name']}")
        if not result.get("allowed"):
            print(f"            Reason: {result.get('reason', 'unknown')}")

    # ----------------------------------------------------------------
    # 3. Check risk levels
    # ----------------------------------------------------------------
    print("\n[2] Risk level assessment...")

    risk_levels = ["none", "low", "medium", "high"]
    for level in risk_levels:
        result = policy.evaluate_action(
            action_type="custom_action",
            context={"risk_level": level}
        )
        status = "✓" if result.get("allowed") else "✗"
        print(f"    {status} Risk level '{level}': {result.get('allowed')}")

    # ----------------------------------------------------------------
    # 4. Get policy summary
    # ----------------------------------------------------------------
    print("\n[3] Policy summary...")
    summary = policy.get_summary()
    print(f"    Forbidden actions: {summary.get('forbidden_actions', [])}")
    print(f"    Risk threshold: {summary.get('default_risk_threshold', 'unknown')}")
    print(f"    Limits: {summary.get('limits', {})}")

    # ----------------------------------------------------------------
    # Done
    # ----------------------------------------------------------------
    print("\n" + "=" * 60)
    print("Custom policy example completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
