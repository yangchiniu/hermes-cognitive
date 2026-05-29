"""
Resource Usage Metrics Tracker

Tracks CPU, memory, thread, and file descriptor usage over time for
stability and endurance testing. Detects leaks and provides summary statistics.

Standard library only; uses psutil if available, falls back to /proc.
"""

import os
import time
import json
import math
import statistics
from pathlib import Path
from collections import defaultdict


# ---------------------------------------------------------------------------
# Resource sampling (psutil preferred, /proc fallback)
# ---------------------------------------------------------------------------

def _read_proc_status():
    """Read resource usage from /proc/self/status (Linux only)."""
    stats = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip()
                    # Parse memory values (kB)
                    if key in ("VmRSS", "VmSize", "VmPeak"):
                        stats[key.lower()] = int(val.split()[0]) * 1024  # kB -> bytes
                    elif key == "Threads":
                        stats["threads"] = int(val)
                    elif key == "FDSize":
                        stats["fd_size"] = int(val)
        # Read CPU from /proc/self/stat
        with open("/proc/self/stat") as f:
            fields = f.read().split()
            # field 13 = utime, field 14 = stime (jiffies)
            if len(fields) > 14:
                try:
                    utime = int(fields[13])
                    stime = int(fields[14])
                    stats["cpu_time_user"] = utime
                    stats["cpu_time_system"] = stime
                except (ValueError, IndexError):
                    pass
    except (OSError, IOError, IndexError, ValueError):
        pass
    return stats


def _sample_resources():
    """
    Sample current resource usage.

    Returns dict with keys: memory_rss, memory_vms, cpu_percent,
                            threads, fds, timestamp, uptime_s
    """
    result = {
        "timestamp": time.time(),
        "uptime_s": time.time() - _sample_resources._start_time,
    }

    # Try psutil first
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        result["memory_rss"] = mem.rss
        result["memory_vms"] = mem.vms
        result["cpu_percent"] = proc.cpu_percent(interval=0.1)
        result["threads"] = proc.num_threads()
        try:
            result["fds"] = proc.num_fds()
        except (psutil.AccessDenied, AttributeError):
            result["fds"] = -1

        # System-level memory
        sys_mem = psutil.virtual_memory()
        result["system_memory_percent"] = sys_mem.percent
        result["system_memory_available"] = sys_mem.available
    except (ImportError, AttributeError):
        # Fall back to /proc
        proc_stats = _read_proc_status()
        result["memory_rss"] = proc_stats.get("vmrss", -1)
        result["memory_vms"] = proc_stats.get("vmsize", -1)
        result["threads"] = proc_stats.get("threads", -1)
        result["fds"] = proc_stats.get("fd_size", -1)
        result["cpu_percent"] = -1
        result["system_memory_percent"] = -1
        result["system_memory_available"] = -1

    return result


_sample_resources._start_time = time.time()


# ---------------------------------------------------------------------------
# MetricsTracker
# ---------------------------------------------------------------------------

class MetricsTracker:
    """
    Track resource usage over time.

    Usage:
        tracker = MetricsTracker()
        tracker.record(_sample_resources())
        summary = tracker.get_summary()
        leaks = tracker.detect_leak()
        tracker.export("/tmp/metrics.json")
    """

    def __init__(self, window_size=None):
        """
        Args:
            window_size: Max number of samples to keep (None = unlimited).
        """
        self._samples = []
        self._window_size = window_size
        self._start_time = time.time()

    def record(self, resources):
        """
        Record a resource snapshot.

        Args:
            resources: dict from _sample_resources() or similar.
        """
        entry = dict(resources)
        entry["recorded_at"] = time.time()
        entry["elapsed_s"] = entry["recorded_at"] - self._start_time
        self._samples.append(entry)

        if self._window_size and len(self._samples) > self._window_size:
            self._samples.pop(0)

    def get_summary(self):
        """
        Return min, max, avg, trend for each numeric metric.

        Returns dict mapping metric names to {min, max, avg, median, latest, trend}.
        """
        if not self._samples:
            return {}

        # Identify numeric metrics
        sample = self._samples[0]
        metric_names = [
            k for k, v in sample.items()
            if isinstance(v, (int, float)) and k not in ("timestamp", "recorded_at", "elapsed_s")
        ]

        summary = {}
        for name in metric_names:
            values = [s.get(name, 0) for s in self._samples if isinstance(s.get(name), (int, float))]
            if not values:
                continue

            # Basic stats
            avg = statistics.mean(values)
            summary[name] = {
                "min": min(values),
                "max": max(values),
                "avg": round(avg, 2),
                "median": round(statistics.median(values), 2),
                "latest": values[-1],
                "samples": len(values),
            }

            # Trend: slope of last N samples (linear regression)
            if len(values) >= 10:
                n = min(20, len(values))
                recent = values[-n:]
                x_vals = list(range(n))
                try:
                    slope = _linear_regression_slope(x_vals, recent)
                    summary[name]["trend"] = round(slope, 4)
                    summary[name]["trend_direction"] = "increasing" if slope > 0.01 else ("decreasing" if slope < -0.01 else "stable")
                except (ValueError, StatisticsError) as e:
                    summary[name]["trend"] = 0.0
                    summary[name]["trend_direction"] = "unknown"
            else:
                summary[name]["trend"] = 0.0
                summary[name]["trend_direction"] = "insufficient_data"

        return summary

    def detect_leak(self):
        """
        Check for memory/thread/fd leaks.

        Returns list of warning strings describing potential leaks.
        """
        warnings = []
        if len(self._samples) < 10:
            return warnings  # Not enough data

        summary = self.get_summary()

        # Check memory RSS trend
        mem_info = summary.get("memory_rss", {})
        if mem_info.get("trend_direction") == "increasing" and mem_info.get("trend", 0) > 100:
            # Memory increasing at >100 bytes per sample — potential leak
            samples = mem_info.get("samples", 0)
            trend = mem_info.get("trend", 0)
            total_increase = trend * samples
            if total_increase > 1024 * 1024:  # >1MB total increase
                warnings.append(
                    f"Potential memory leak: RSS increasing at {trend:.0f} bytes/sample "
                    f"({total_increase / 1024:.0f}KB total over {samples} samples)"
                )

        # Check thread count trend
        thread_info = summary.get("threads", {})
        if thread_info.get("trend_direction") == "increasing" and thread_info.get("trend", 0) > 0.1:
            # Threads increasing
            trend = thread_info.get("trend", 0)
            samples = thread_info.get("samples", 0)
            total_growth = trend * samples
            if total_growth > 2:  # More than 2 threads created
                warnings.append(
                    f"Potential thread leak: thread count increasing at {trend:.2f}/sample "
                    f"({total_growth:.0f} threads over {samples} samples)"
                )

        # Check FD trend
        fd_info = summary.get("fds", {})
        if fd_info.get("trend_direction") == "increasing" and fd_info.get("trend", 0) > 1:
            trend = fd_info.get("trend", 0)
            samples = fd_info.get("samples", 0)
            total_growth = trend * samples
            if total_growth > 10:
                warnings.append(
                    f"Potential FD leak: file descriptors increasing at {trend:.2f}/sample "
                    f"({total_growth:.0f} FDs over {samples} samples)"
                )

        return warnings

    def export(self, path):
        """
        Export metrics data to a JSON file.

        Args:
            path: File path to write to.
        """
        data = {
            "metadata": {
                "start_time": self._start_time,
                "end_time": time.time(),
                "duration_s": time.time() - self._start_time,
                "samples": len(self._samples),
            },
            "summary": self.get_summary(),
            "leak_warnings": self.detect_leak(),
            "raw_samples": self._samples[-100:] if len(self._samples) > 100 else self._samples,
        }

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))

    def clear(self):
        """Clear all recorded samples."""
        self._samples.clear()
        self._start_time = time.time()

    @property
    def sample_count(self):
        return len(self._samples)

    @property
    def duration(self):
        return time.time() - self._start_time

    def __len__(self):
        return len(self._samples)

    def __iter__(self):
        return iter(self._samples)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linear_regression_slope(x_vals, y_vals):
    """Compute the slope of a simple linear regression."""
    n = len(x_vals)
    if n < 2:
        return 0.0

    sum_x = sum(x_vals)
    sum_y = sum(y_vals)
    sum_xy = sum(a * b for a, b in zip(x_vals, y_vals))
    sum_xx = sum(a * a for a in x_vals)

    denominator = n * sum_xx - sum_x * sum_x
    if denominator == 0:
        return 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    return slope


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def create_tracker():
    """Create and return a pre-configured MetricsTracker."""
    return MetricsTracker()


def sample_now():
    """Take an immediate resource sample."""
    return _sample_resources()
