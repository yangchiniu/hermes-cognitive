#!/usr/bin/env python3
"""Environment check script for hermes-cognitive.

Verifies that the system meets all requirements for running hermes-cognitive.
"""

from __future__ import annotations

import sys
import shutil
import platform
from pathlib import Path


def check_python_version() -> bool:
    """Check Python version >= 3.11."""
    major, minor = sys.version_info[:2]
    ok = major >= 3 and minor >= 11
    status = "✓" if ok else "✗"
    print(f"  {status} Python {major}.{minor} (requires >= 3.11)")
    return ok


def check_required_packages() -> bool:
    """Check that required packages are installed."""
    required = ["yaml", "numpy"]
    all_ok = True
    for pkg in required:
        try:
            __import__(pkg)
            print(f"  ✓ {pkg}")
        except ImportError:
            print(f"  ✗ {pkg} (not installed)")
            all_ok = False
    return all_ok


def check_optional_packages() -> bool:
    """Check optional packages (informational only)."""
    optional = {
        "openai": "LLM integration",
        "tiktoken": "Token counting",
        "sentence_transformers": "Semantic retrieval",
        "sklearn": "Machine learning utilities",
    }
    for pkg, desc in optional.items():
        try:
            __import__(pkg)
            print(f"  ✓ {pkg} ({desc})")
        except ImportError:
            print(f"  ○ {pkg} ({desc}) [optional]")
    return True


def check_disk_space() -> bool:
    """Check available disk space."""
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        ok = free_gb >= 1.0
        status = "✓" if ok else "✗"
        print(f"  {status} Disk space: {free_gb:.1f} GB free (requires >= 1 GB)")
        return ok
    except Exception:
        print("  ○ Disk space: unable to check")
        return True


def check_memory() -> bool:
    """Check available memory (Linux only)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_kb = int(line.split()[1])
                    mem_gb = mem_kb / (1024 ** 2)
                    ok = mem_gb >= 1.0
                    status = "✓" if ok else "✗"
                    print(f"  {status} Memory: {mem_gb:.1f} GB total (requires >= 1 GB)")
                    return ok
    except Exception:
        print("  ○ Memory: unable to check (non-Linux)")
        return True
    return True


def check_hermes_core() -> bool:
    """Check that hermes_core can be imported."""
    try:
        from hermes_core.core import __version__
        print(f"  ✓ hermes_core v{__version__}")
        return True
    except ImportError as e:
        print(f"  ✗ hermes_core: {e}")
        return False


def main() -> int:
    """Run all checks."""
    print("=" * 50)
    print("hermes-cognitive Environment Check")
    print("=" * 50)
    print()

    checks = [
        ("Python Version", check_python_version),
        ("Required Packages", check_required_packages),
        ("Optional Packages", check_optional_packages),
        ("Disk Space", check_disk_space),
        ("Memory", check_memory),
        ("hermes-cognitive Import", check_hermes_core),
    ]

    results = []
    for name, check_fn in checks:
        print(f"[{name}]")
        try:
            result = check_fn()
        except Exception as e:
            print(f"  ✗ Error: {e}")
            result = False
        results.append(result)
        print()

    print("=" * 50)
    passed = sum(results)
    total = len(results)
    if all(results):
        print(f"✓ All checks passed ({passed}/{total})")
        print("  hermes-cognitive is ready to use!")
        return 0
    else:
        print(f"✗ Some checks failed ({passed}/{total})")
        print("  Please fix the issues above before proceeding.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
