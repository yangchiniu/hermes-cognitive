"""
Benchmark Report Generator

Produces formatted benchmark reports from benchmark results data.

Standard library only; no external dependencies.
"""

import json
import textwrap
from datetime import datetime


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _bar_chart(value, max_value, width=40):
    """Generate a simple ASCII bar chart segment."""
    if max_value <= 0:
        return " " * width
    filled = int((value / max_value) * width)
    return "█" * filled + "░" * (width - filled)


def _format_duration(seconds):
    """Format seconds into a human-readable string."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.2f}s"
    else:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.0f}s"


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results):
    """
    Generate a formatted benchmark report.

    Args:
        results: dict from run_benchmark() with keys:
            - scenarios (list of scenario result dicts)
            - summary (dict with totals)

    Returns:
        str: Formatted report text.
    """
    scenarios = results.get("scenarios", [])
    summary = results.get("summary", {})

    report_lines = []
    report_lines.append("=" * 72)
    report_lines.append("HERMES CORE — BENCHMARK REPORT")
    report_lines.append("=" * 72)
    report_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"Scenarios: {summary.get('scenarios', 0)}")
    report_lines.append(f"Total runs: {summary.get('total_runs', 0)}")
    report_lines.append(f"Success rate: {summary.get('success_rate', 0)}%")
    report_lines.append(f"Average duration: {_format_duration(summary.get('avg_duration_s', 0))}")
    report_lines.append("")

    # --- Overall summary bar ---
    total = summary.get('total_runs', 0) or 1
    passed = summary.get('passed', 0)
    failed = summary.get('failed', 0)
    timed_out = summary.get('timed_out', 0)

    report_lines.append("Overall Results:")
    report_lines.append(f"  Passed:     {passed:>4}  {_bar_chart(passed, total)}")
    report_lines.append(f"  Failed:     {failed:>4}  {_bar_chart(failed, total)}")
    report_lines.append(f"  Timed out:  {timed_out:>4}  {_bar_chart(timed_out, total)}")
    report_lines.append(f"  Total:      {total:>4}")
    report_lines.append("")

    # --- Per-scenario detail ---
    report_lines.append("-" * 72)
    report_lines.append("Per-Scenario Results")
    report_lines.append("-" * 72)

    if not scenarios:
        report_lines.append("  No scenarios executed.")
        report_lines.append("")

    for s in scenarios:
        name = s.get("name", "unknown")
        goal = s.get("goal", "")
        success_count = s.get("success_count", 0)
        fail_count = s.get("fail_count", 0)
        iterations = s.get("iterations", 1)
        success_rate = s.get("success_rate", 0)
        avg_dur = s.get("avg_duration_s", 0)
        min_dur = s.get("min_duration_s", 0)
        max_dur = s.get("max_duration_s", 0)
        tool_cov = s.get("tool_coverage", False)
        timeouts = s.get("timeout_count", 0)

        # Pass/fail indicator
        status_icon = "✓" if success_count == iterations else "✗"
        report_lines.append(f"\n  {status_icon} {name}")
        report_lines.append(f"      Goal: {textwrap.shorten(goal, width=60)}")
        report_lines.append(f"      Results: {success_count}/{iterations} passed ({success_rate}%)")

        # Duration bar
        max_dur_all = max(s.get("max_duration_s", 0) for s in scenarios) or 1
        dur_bar = _bar_chart(avg_dur, max_dur_all, 30)
        report_lines.append(f"      Duration: avg={_format_duration(avg_dur)} "
                          f"[{_format_duration(min_dur)} .. {_format_duration(max_dur)}]")
        report_lines.append(f"      Timeline: {dur_bar}")

        if timeouts > 0:
            report_lines.append(f"      ⚠  {timeouts} timeout(s)")

        if not tool_cov:
            report_lines.append(f"      ⚠  Tool coverage incomplete")

        # Show per-iteration breakdown
        individual = s.get("results", [])
        if len(individual) > 1:
            idx_bar = ""
            for r in individual:
                idx_bar += "█" if r.get("success", False) else "░"
            report_lines.append(f"      Iterations: [{idx_bar}]")

    # --- Summary ---
    report_lines.append("")
    report_lines.append("-" * 72)
    report_lines.append("SUMMARY")
    report_lines.append("-" * 72)

    success_rate = summary.get("success_rate", 0)
    avg_dur = summary.get("avg_duration_s", 0)

    if success_rate >= 90:
        grade = "EXCELLENT"
    elif success_rate >= 80:
        grade = "GOOD"
    elif success_rate >= 70:
        grade = "ACCEPTABLE"
    else:
        grade = "NEEDS IMPROVEMENT"

    report_lines.append(f"  Grade: {grade}")
    report_lines.append(f"  Overall success rate: {success_rate}%")
    report_lines.append(f"  Average duration: {_format_duration(avg_dur)}")
    report_lines.append(f"  Scenarios with failures: {summary.get('failed', 0)}")
    report_lines.append(f"  Timeouts: {summary.get('timed_out', 0)}")
    report_lines.append("")
    report_lines.append("=" * 72)

    return "\n".join(report_lines)


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_json(results, path):
    """
    Export benchmark results to a JSON file.

    Args:
        results: dict from run_benchmark().
        path: File path to write to.
    """
    path = Path(path) if isinstance(path, str) else path
    path.parent.mkdir(parents=True, exist_ok=True)

    export_data = {
        "timestamp": datetime.now().isoformat(),
        "summary": results.get("summary", {}),
        "scenarios": [],
    }

    for s in results.get("scenarios", []):
        export_data["scenarios"].append({
            "name": s.get("name"),
            "goal": s.get("goal"),
            "success_rate": s.get("success_rate"),
            "avg_duration_s": s.get("avg_duration_s"),
            "min_duration_s": s.get("min_duration_s"),
            "max_duration_s": s.get("max_duration_s"),
            "timeout_count": s.get("timeout_count", 0),
        })

    path.write_text(json.dumps(export_data, indent=2, default=str))
    return str(path)


def compare_reports(report1_path, report2_path):
    """
    Compare two benchmark reports and show regressions/improvements.

    Args:
        report1_path: Path to first JSON report.
        report2_path: Path to second JSON report.

    Returns:
        str: Comparison report text.
    """
    try:
        with open(report1_path) as f:
            r1 = json.load(f)
        with open(report2_path) as f:
            r2 = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return f"Error loading reports: {e}"

    lines = []
    lines.append("=" * 60)
    lines.append("BENCHMARK COMPARISON")
    lines.append("=" * 60)

    s1 = r1.get("summary", {})
    s2 = r2.get("summary", {})

    rate_diff = s2.get("success_rate", 0) - s1.get("success_rate", 0)
    dur_diff = s2.get("avg_duration_s", 0) - s1.get("avg_duration_s", 0)

    lines.append(f"  Success rate: {s1.get('success_rate', 0)}% → {s2.get('success_rate', 0)}% "
                f"({'▲' if rate_diff > 0 else '▼'} {abs(rate_diff):.1f}%)")
    lines.append(f"  Avg duration: {s1.get('avg_duration_s', 0):.2f}s → {s2.get('avg_duration_s', 0):.2f}s "
                f"({'▼' if dur_diff < 0 else '▲'} {abs(dur_diff):.2f}s)")

    # Per-scenario comparison
    sc1 = {s["name"]: s for s in r1.get("scenarios", [])}
    sc2 = {s["name"]: s for s in r2.get("scenarios", [])}
    all_names = set(sc1.keys()) | set(sc2.keys())

    if all_names:
        lines.append(f"\n{'=' * 60}")
        lines.append("Per-Scenario Changes")
        lines.append(f"{'=' * 60}")

        for name in sorted(all_names):
            old = sc1.get(name, {})
            new = sc2.get(name, {})

            old_rate = old.get("success_rate", 0)
            new_rate = new.get("success_rate", 0)
            old_dur = old.get("avg_duration_s", 0)
            new_dur = new.get("avg_duration_s", 0)

            rate_change = new_rate - old_rate
            dur_change = new_dur - old_dur

            if rate_change != 0 or abs(dur_change) > 0.1:
                arrow_rate = "▲" if rate_change > 0 else "▼"
                arrow_dur = "▼" if dur_change < 0 else "▲"
                lines.append(f"\n  {name}:")
                lines.append(f"    Rate: {old_rate}% → {new_rate}% {arrow_rate} ({rate_change:+.1f}%)")
                lines.append(f"    Duration: {old_dur:.2f}s → {new_dur:.2f}s {arrow_dur} ({dur_change:+.2f}s)")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)
