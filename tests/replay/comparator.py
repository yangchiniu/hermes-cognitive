"""
Result Comparator

Compares original task execution results against replayed results to
detect differences and regressions.

Standard library only; no external dependencies.
"""

import json
import hashlib
from pathlib import Path


# ---------------------------------------------------------------------------
# Comparison utilities
# ---------------------------------------------------------------------------

def _get_stable_hash(obj):
    """Compute a deterministic hash of any JSON-serializable object."""
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        serialized = str(obj)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _flatten_dict(d, prefix=""):
    """
    Flatten a nested dict into dot-separated key paths.

    Example: {"a": {"b": 1}} -> {"a.b": 1}
    """
    items = []
    if isinstance(d, dict):
        for k, v in d.items():
            new_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                items.extend(_flatten_dict(v, new_key).items())
            else:
                items.append((new_key, v))
    elif isinstance(d, (list, tuple)):
        for i, v in enumerate(d):
            new_key = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                items.extend(_flatten_dict(v, new_key).items())
            else:
                items.append((new_key, v))
    else:
        items.append((prefix, d))
    return dict(items)


# ---------------------------------------------------------------------------
# Main comparison
# ---------------------------------------------------------------------------

def compare_results(original, replayed):
    """
    Compare original vs replayed results for regression detection.

    Args:
        original: dict — result from first execution.
        replayed: dict — result from re-execution.

    Returns:
        dict with keys:
            match (bool): True if results are functionally identical.
            original_hash (str): Hash of original.
            replay_hash (str): Hash of replayed.
            differences (list): Human-readable differences.
            match_ratio (float): 0.0 to 1.0 similarity score.
    """
    # Handle None or non-dict inputs
    if original is None and replayed is None:
        return {
            "match": True,
            "original_hash": "none",
            "replay_hash": "none",
            "differences": [],
            "match_ratio": 1.0,
        }

    if original is None:
        return {
            "match": False,
            "original_hash": "none",
            "replay_hash": _get_stable_hash(replayed),
            "differences": ["Original result is None, replayed is present"],
            "match_ratio": 0.0,
        }

    if replayed is None:
        return {
            "match": False,
            "original_hash": _get_stable_hash(original),
            "replay_hash": "none",
            "differences": ["Replayed result is None, original is present"],
            "match_ratio": 0.0,
    }

    if not isinstance(original, dict) or not isinstance(replayed, dict):
        # Simple comparison for non-dict values
        match = original == replayed
        return {
            "match": match,
            "original_hash": _get_stable_hash(original),
            "replay_hash": _get_stable_hash(replayed),
            "differences": [] if match else [f"Values differ: {original!r} vs {replayed!r}"],
            "match_ratio": 1.0 if match else 0.0,
        }

    # Compare high-level success first
    orig_success = original.get("success", original.get("status", True))
    replay_success = replayed.get("success", replayed.get("status", True))

    if isinstance(orig_success, str):
        orig_success = orig_success.lower() in ("true", "success", "ok", "yes")
    if isinstance(replay_success, str):
        replay_success = replay_success.lower() in ("true", "success", "ok", "yes")

    # Flatten both for detailed comparison
    try:
        orig_flat = _flatten_dict(original)
        replay_flat = _flatten_dict(replayed)
    except Exception:
        # If flattening fails, compare hashes directly
        orig_hash = _get_stable_hash(original)
        replay_hash = _get_stable_hash(replayed)
        return {
            "match": orig_hash == replay_hash,
            "original_hash": orig_hash,
            "replay_hash": replay_hash,
            "differences": [] if orig_hash == replay_hash else ["Hash mismatch (flattening failed)"],
            "match_ratio": 1.0 if orig_hash == replay_hash else 0.0,
        }

    # Compute hashes
    orig_hash = _get_stable_hash(original)
    replay_hash = _get_stable_hash(replayed)

    # Find differences
    differences = []

    # Check keys present in one but not the other
    orig_keys = set(orig_flat.keys())
    replay_keys = set(replay_flat.keys())

    added_keys = replay_keys - orig_keys
    removed_keys = orig_keys - replay_keys

    for key in sorted(added_keys):
        differences.append(f"New key in replayed: '{key}' = {replay_flat[key]!r}")

    for key in sorted(removed_keys):
        differences.append(f"Missing key in replayed: '{key}' (was {orig_flat[key]!r})")

    # Check values for common keys
    common_keys = orig_keys & replay_keys
    changed = 0
    total = len(common_keys) or 1

    for key in sorted(common_keys):
        ov = orig_flat[key]
        rv = replay_flat[key]

        # Skip timestamps and durations (expected to differ)
        if any(ts_kw in key.lower() for ts_kw in ["timestamp", "duration", "time", "uptime"]):
            continue

        if ov != rv:
            changed += 1
            if len(differences) < 20:  # Cap differences
                differences.append(f"'{key}': {ov!r} -> {rv!r}")

    # Compute match ratio
    match_ratio = 1.0 - (changed / total) if total > 0 else 1.0

    # Also check top-level success
    if orig_success != replay_success:
        differences.append(f"Success status differs: original={orig_success}, replayed={replay_success}")

    match = (
        orig_hash == replay_hash
        or (len(differences) == 0 and orig_success == replay_success)
    )

    return {
        "match": match,
        "original_hash": orig_hash,
        "replay_hash": replay_hash,
        "differences": differences[:30],  # Cap at 30
        "match_ratio": round(match_ratio, 4),
    }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def format_comparison(comparison):
    """Format a comparison result as a readable string."""
    lines = []
    if comparison["match"]:
        lines.append("✓ MATCH")
    else:
        lines.append("✗ MISMATCH")

    lines.append(f"  Original hash: {comparison['original_hash']}")
    lines.append(f"  Replay hash:   {comparison['replay_hash']}")
    lines.append(f"  Match ratio:   {comparison['match_ratio']:.1%}")

    if comparison["differences"]:
        lines.append(f"  Differences ({len(comparison['differences'])}):")
        for d in comparison["differences"][:10]:
            lines.append(f"    - {d}")

    return "\n".join(lines)
