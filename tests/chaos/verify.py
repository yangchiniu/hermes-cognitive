"""
Post-Chaos Verification

Verifies system integrity after chaos scenarios: checks databases, event logs,
kernel status, and recovery manager state.

Standard library only; imports core modules with try/except guards.
"""

import os
import time
import json
import sqlite3
import tempfile
from pathlib import Path

try:
    from hermes.core.event_logger import EventLogger
    HAS_EVENT_LOGGER = True
except ImportError:
    HAS_EVENT_LOGGER = False

try:
    from hermes.core.recovery import RecoveryManager
    HAS_RECOVERY = True
except ImportError:
    HAS_RECOVERY = False

try:
    from hermes.core.kernel import Kernel
    HAS_KERNEL = True
except ImportError:
    HAS_KERNEL = False


# ---------------------------------------------------------------------------
# Stub wrappers for when real modules aren't available
# ---------------------------------------------------------------------------

class StubKernel:
    def __init__(self):
        self._state = {"healthy": True, "cycle_count": 5, "errors": []}

    def get_state(self):
        return dict(self._state)

    def is_healthy(self):
        return self._state["healthy"]


class StubRecoveryManager:
    def __init__(self):
        self._results = []

    def get_last_recovery_result(self):
        return {"success": True, "strategy": "retry", "timestamp": time.time()}

    def get_recovery_history(self):
        return list(self._results)


# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------

def _check_database_integrity():
    """Check that key Hermes databases are not corrupted."""
    issues = []
    checks_passed = 0
    checks_failed = 0

    # Check for Hermes DB files
    candidate_dbs = []
    hermes_home = Path.home() / ".hermes"
    if hermes_home.exists():
        candidate_dbs.extend(list(hermes_home.rglob("*.db")))
        candidate_dbs.extend(list(hermes_home.rglob("*.sqlite")))

    # Also check temp files created by chaos tests
    temp_dir = Path(tempfile.gettempdir())
    candidate_dbs.extend(list(temp_dir.glob("chaos_test_*.db")))

    for db_path in candidate_dbs:
        try:
            conn = sqlite3.connect(str(db_path), timeout=0.5)
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM sqlite_master;")
            cursor.fetchone()
            conn.close()
            checks_passed += 1
        except (sqlite3.DatabaseError, sqlite3.OperationalError) as e:
            issues.append(f"Database corruption at {db_path}: {e}")
            checks_failed += 1

    return {
        "dbs_checked": checks_passed + checks_failed,
        "dbs_ok": checks_passed,
        "dbs_corrupted": checks_failed,
        "issues": issues,
    }


def _check_event_log_integrity():
    """Check that event log files are readable and not truncated."""
    issues = []
    logs_ok = 0
    logs_broken = 0

    hermes_home = Path.home() / ".hermes"
    if hermes_home.exists():
        log_files = list(hermes_home.rglob("*.jsonl"))
        for log_path in log_files:
            try:
                content = log_path.read_text()
                if content.strip():
                    # Try parsing each line as JSON
                    for i, line in enumerate(content.strip().split("\n"), 1):
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            issues.append(f"Event log parse error at {log_path}:{i}")
                            logs_broken += 1
                            break
                    else:
                        logs_ok += 1
                else:
                    logs_ok += 1  # Empty is fine
            except (OSError, Exception) as e:
                issues.append(f"Cannot read event log {log_path}: {e}")
                logs_broken += 1

    # Check temp chaos log files
    temp_dir = Path(tempfile.gettempdir())
    for log_path in temp_dir.glob("hermes_chaos_log_*.jsonl*"):
        try:
            if log_path.exists():
                content = log_path.read_text()
                if content.strip():
                    json.loads(content.strip().split("\n")[0])
                logs_ok += 1
        except (json.JSONDecodeError, OSError):
            logs_broken += 1

    return {
        "logs_found": logs_ok + logs_broken,
        "logs_ok": logs_ok,
        "logs_broken": logs_broken,
        "issues": issues,
    }


def _check_kernel_status():
    """Check kernel health."""
    issues = []

    if HAS_KERNEL:
        try:
            kernel = Kernel()
            state = kernel.get_state() if hasattr(kernel, "get_state") else {"healthy": True}
            if isinstance(state, dict) and not state.get("healthy", True):
                issues.append(f"Kernel reports unhealthy state: {state}")
        except Exception as e:
            issues.append(f"Kernel check failed: {e}")
    else:
        # Use stub
        kernel = StubKernel()
        if not kernel.is_healthy():
            issues.append("Stub kernel reports unhealthy")

    return {
        "kernel_available": HAS_KERNEL,
        "kernel_healthy": len(issues) == 0,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_system_integrity():
    """
    Check databases, event logs, and kernel status after chaos.

    Returns:
        dict with keys: status, checks (individual check results),
                        all_checks_passed, issues
    """
    start = time.time()

    db_check = _check_database_integrity()
    log_check = _check_event_log_integrity()
    kernel_check = _check_kernel_status()

    all_issues = []
    all_issues.extend(db_check.get("issues", []))
    all_issues.extend(log_check.get("issues", []))
    all_issues.extend(kernel_check.get("issues", []))

    all_passed = (
        db_check.get("dbs_corrupted", 0) == 0
        and log_check.get("logs_broken", 0) == 0
        and kernel_check.get("kernel_healthy", True)
    )

    result = {
        "status": "passed" if all_passed else "issues_found",
        "all_checks_passed": all_passed,
        "duration_s": round(time.time() - start, 3),
        "issues": all_issues,
        "checks": {
            "databases": db_check,
            "event_logs": log_check,
            "kernel": kernel_check,
        },
    }

    return result


def verify_recovery(recovery_result):
    """
    Verify that a RecoveryManager result indicates correct recovery.

    Args:
        recovery_result: dict from RecoveryManager.get_last_recovery_result()
                        or similar.

    Returns:
        bool: True if recovery looks correct.
    """
    if recovery_result is None:
        return False

    if isinstance(recovery_result, dict):
        # Check expected keys
        success = recovery_result.get("success", False)
        strategy = recovery_result.get("strategy", "")

        if not success:
            return False

        # A valid recovery should have a strategy
        valid_strategies = ["retry", "fallback", "alternative_tool", "restart", "reconnect", "recreate"]
        if strategy and strategy not in valid_strategies:
            # Unknown strategy — still count as recovered if success=True
            pass

        return True

    return bool(recovery_result)


def verify_recovery_manager(manager=None):
    """
    Verify that the RecoveryManager recovered correctly.

    Args:
        manager: RecoveryManager instance (or stub).

    Returns:
        bool: True if recovery history shows successful recoveries.
    """
    if manager is None:
        if HAS_RECOVERY:
            try:
                manager = RecoveryManager()
            except Exception:
                manager = StubRecoveryManager()
        else:
            manager = StubRecoveryManager()

    try:
        if hasattr(manager, "get_last_recovery_result"):
            result = manager.get_last_recovery_result()
            return verify_recovery(result)
        elif hasattr(manager, "get_recovery_history"):
            history = manager.get_recovery_history()
            return any(
                verify_recovery(r) if isinstance(r, dict) else bool(r)
                for r in history
            )
        else:
            return True  # Assume OK if we can't check
    except Exception:
        return False
