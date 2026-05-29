"""
telemetry_replay.py — Replay, analyze, and report on telemetry event logs.

Loads telemetry events from the NDJSON event log (~/.hermes/core/data/agent_events.ndjson
by default), replays them chronologically, generates reports, detects anomalies,
and produces ASCII timelines.

Dependencies (optional, imported with try/except):
    event_logger.py, telemetry.py
"""

from __future__ import annotations

import json
import pathlib
import threading
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Generator, Optional

# ---------------------------------------------------------------------------
# Optional dependency imports
# ---------------------------------------------------------------------------

try:
    from . import telemetry as _telemetry_mod
    TelemetryData = _telemetry_mod.TelemetryData  # type: ignore[misc]
except (ImportError, ModuleNotFoundError, AttributeError):
    # Fallback dataclass matching telemetry.py's TelemetryData
    @dataclass
    class TelemetryData:  # type: ignore[no-redef]
        """Minimal fallback TelemetryData if telemetry.py is unavailable."""
        timestamp: str
        cpu_load: float = 0.0
        ram_percent: float = 0.0
        disk_percent: float = 0.0
        planner_depth: int = 0
        memory_count: int = 0
        memory_health: float = 1.0
        event_throughput: float = 0.0
        task_latency_avg: float = 0.0
        recovery_frequency: float = 0.0
        active_goals: int = 0
        thread_count: int = 0
        cognitive_stability_score: float = 1.0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_EVENT_LOG = (
    pathlib.Path.home() / ".hermes" / "core" / "data" / "agent_events.ndjson"
)
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_ONION = chr(0x2588)  # full block █ for ASCII charts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp string, handling optional sub-second precision."""
    text = text.strip()
    # Normalise Z to +00:00
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Remove trailing microseconds if the format is strange
    try:
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        # Try common NDJSON formats
        for fmt in (
            _ISO_FORMAT,
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f+00:00",
        ):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
    return datetime.min.replace(tzinfo=timezone.utc)


def _parse_range_arg(
    time_range: Optional[tuple],
) -> tuple[Optional[datetime], Optional[datetime]]:
    """Convert a (start_str, end_str) tuple into datetime objects."""
    if time_range is None:
        return None, None
    start = None
    end = None
    if len(time_range) >= 1 and time_range[0]:
        start = _parse_iso(time_range[0])
    if len(time_range) >= 2 and time_range[1]:
        end = _parse_iso(time_range[1])
    return start, end


def _event_dict_to_telemetry(record: dict) -> TelemetryData:
    """Convert an event-log dict into a TelemetryData snapshot.

    Handles two shapes:
      1) Direct telemetry snapshot (keys match TelemetryData fields).
      2) ``telemetry.collected`` events where fields are nested in ``data``.
    """
    data_payload = record.get("data") or {}
    ts = record.get("timestamp", "")

    # Prefer top-level fields for telemetry events; fall back to nested data.
    source = record if "cognitive_stability_score" in record else data_payload

    return TelemetryData(
        timestamp=ts,
        cpu_load=float(source.get("cpu_load", 0.0)),
        ram_percent=float(source.get("ram_percent", 0.0)),
        disk_percent=float(source.get("disk_percent", 0.0)),
        planner_depth=int(source.get("planner_depth", 0)),
        memory_count=int(source.get("memory_count", 0)),
        memory_health=float(source.get("memory_health", 1.0)),
        event_throughput=float(source.get("event_throughput", 0.0)),
        task_latency_avg=float(source.get("task_latency_avg", 0.0)),
        recovery_frequency=float(source.get("recovery_frequency", 0.0)),
        active_goals=int(source.get("active_goals", 0)),
        thread_count=int(source.get("thread_count", 0)),
        cognitive_stability_score=float(
            source.get("cognitive_stability_score", 1.0)
        ),
    )


def _is_telemetry_event(record: dict) -> bool:
    """Return True if the event record looks like a telemetry snapshot."""
    # Direct telemetry snapshot
    if "cognitive_stability_score" in record:
        return True
    # telemetry.collected event from event_logger
    if record.get("event_type") in (
        "telemetry.collected",
        "telemetry.snapshot",
        "telemetry.alert",
    ):
        return True
    # Check data payload
    data_payload = record.get("data", {})
    if isinstance(data_payload, dict) and "cognitive_stability_score" in data_payload:
        return True
    return False


# ---------------------------------------------------------------------------
# TelemetryReplay
# ---------------------------------------------------------------------------


class TelemetryReplay:
    """Singleton that loads, replays, and analyses telemetry event logs.

    Usage::

        replay = TelemetryReplay()
        count = replay.load_from_event_log()
        for tdata in replay.replay():
            print(tdata.cognitive_stability_score)
        report = replay.generate_report()
        replay.export_report("/tmp/report.json")
    """

    _instance: Optional["TelemetryReplay"] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "TelemetryReplay":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    obj = super().__new__(cls)
                    cls._instance = obj
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialised"):
            return
        self._initialised = True
        # All loaded TelemetryData objects, sorted chronologically (oldest first)
        self._events: list[TelemetryData] = []
        # Raw event dicts (for reports that need full event data)
        self._raw_events: list[dict[str, Any]] = []
        # Loaded path
        self._source_path: Optional[pathlib.Path] = None

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------


    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for testing or config change."""
        with _instance_lock:
            globals()['_instance'] = None

    def load_from_event_log(self, path: Optional[str] = None) -> int:
        """Load telemetry events from an NDJSON event log.

        Parameters
        ----------
        path : str or None
            Path to the NDJSON log file.  If ``None``, reads the default
            event log at ``~/.hermes/core/data/agent_events.ndjson``.

        Returns
        -------
        int
            Number of telemetry events loaded.
        """
        if path is None:
            log_path = _DEFAULT_EVENT_LOG
        else:
            log_path = pathlib.Path(path).expanduser().resolve()

        if not log_path.exists():
            return 0

        raw_records: list[dict[str, Any]] = []
        telem_events: list[TelemetryData] = []

        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                raw_records.append(record)
                if _is_telemetry_event(record):
                    try:
                        tdata = _event_dict_to_telemetry(record)
                        telem_events.append(tdata)
                    except (TypeError, ValueError, KeyError):
                        continue

        # Sort chronologically (oldest first)
        telem_events.sort(key=lambda e: _parse_iso(e.timestamp))
        raw_records.sort(key=lambda r: _parse_iso(r.get("timestamp", "")))

        self._events = telem_events
        self._raw_events = raw_records
        self._source_path = log_path

        return len(telem_events)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        speed: float = 1.0,
    ) -> Generator[TelemetryData, None, None]:
        """Yield telemetry events chronologically.

        Parameters
        ----------
        start_time : str or None
            ISO-8601 timestamp; only events at or after this time are yielded.
        end_time : str or None
            ISO-8601 timestamp; only events before or at this time are yielded.
        speed : float
            Playback speed multiplier.  ``1.0`` yields every event; ``10.0``
            yields every 10th event (sampling step = int(speed)).

        Yields
        ------
        TelemetryData
        """
        start_dt = _parse_iso(start_time) if start_time else None
        end_dt = _parse_iso(end_time) if end_time else None
        step = max(1, int(speed))

        for i, ev in enumerate(self._events):
            if i % step != 0:
                continue
            ev_ts = _parse_iso(ev.timestamp)
            if start_dt is not None and ev_ts < start_dt:
                continue
            if end_dt is not None and ev_ts > end_dt:
                continue
            yield ev

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def generate_report(
        self, time_range: Optional[tuple] = None
    ) -> dict[str, Any]:
        """Generate a comprehensive replay report.

        Parameters
        ----------
        time_range : (start, end) tuple of ISO-8601 strings, or None
            Filter events to this time window.

        Returns
        -------
        dict
            Keys: ``time_span``, ``events_total``, ``events_by_type``,
            ``cognitive_stability_timeline``, ``planner_depth_timeline``,
            ``memory_growth_timeline``, ``recovery_events``, ``anomalies``,
            ``summary``.
        """
        start_dt, end_dt = _parse_range_arg(time_range)

        filtered_raw = self._raw_events
        if start_dt is not None or end_dt is not None:
            filtered_raw = [
                r
                for r in filtered_raw
                if (start_dt is None or _parse_iso(r.get("timestamp", "")) >= start_dt)
                and (end_dt is None or _parse_iso(r.get("timestamp", "")) <= end_dt)
            ]

        filtered = self._events
        if start_dt is not None or end_dt is not None:
            filtered = [
                e
                for e in filtered
                if (start_dt is None or _parse_iso(e.timestamp) >= start_dt)
                and (end_dt is None or _parse_iso(e.timestamp) <= end_dt)
            ]

        # Time span
        if len(filtered) >= 2:
            first_ts = _parse_iso(filtered[0].timestamp)
            last_ts = _parse_iso(filtered[-1].timestamp)
            delta = last_ts - first_ts
            time_span_str = self._format_delta(delta)
        elif len(filtered) == 1:
            time_span_str = f"single event at {filtered[0].timestamp}"
        else:
            time_span_str = "no events"

        # Events by type
        by_type: dict[str, int] = defaultdict(int)
        for r in filtered_raw:
            by_type[r.get("event_type", "unknown")] += 1

        # Timelines
        stability_timeline = [
            {"timestamp": e.timestamp, "score": e.cognitive_stability_score}
            for e in filtered
        ]
        depth_timeline = [
            {"timestamp": e.timestamp, "depth": e.planner_depth} for e in filtered
        ]
        memory_timeline = [
            {"timestamp": e.timestamp, "count": e.memory_count} for e in filtered
        ]

        # Recovery events
        recovery_events = []
        for r in filtered_raw:
            ev_type = r.get("event_type", "")
            if "recover" in ev_type.lower():
                data_payload = r.get("data", {})
                recovery_events.append(
                    {
                        "timestamp": r.get("timestamp", ""),
                        "type": ev_type,
                        "success": data_payload.get(
                            "success", data_payload.get("status", "unknown")
                        ),
                    }
                )

        # Anomalies
        anomalies = self.find_anomalies(time_range)

        # Summary
        avg_stability = (
            sum(e.cognitive_stability_score for e in filtered) / len(filtered)
            if filtered
            else 0.0
        )
        min_stability = (
            min(e.cognitive_stability_score for e in filtered) if filtered else 0.0
        )
        max_depth = max(e.planner_depth for e in filtered) if filtered else 0
        stability_summary = (
            f"Average stability: {avg_stability:.3f}, "
            f"minimum: {min_stability:.3f}, "
            f"max planner depth: {max_depth}"
        )
        anomaly_summary = (
            f"{len(anomalies)} anomalies detected" if anomalies else "No anomalies"
        )
        summary = (
            f"Telemetry replay over {time_span_str}: "
            f"{len(filtered)} telemetry events from {len(filtered_raw)} total events. "
            f"{stability_summary}. {anomaly_summary}."
        )

        return {
            "time_span": time_span_str,
            "events_total": len(filtered),
            "events_by_type": dict(by_type),
            "cognitive_stability_timeline": stability_timeline,
            "planner_depth_timeline": depth_timeline,
            "memory_growth_timeline": memory_timeline,
            "recovery_events": recovery_events,
            "anomalies": anomalies,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # ASCII Timeline Plot
    # ------------------------------------------------------------------

    def plot_timeline(
        self, metric: str = "stability", output_path: Optional[str] = None
    ) -> str:
        """Generate an ASCII timeline of a metric over time.

        Parameters
        ----------
        metric : str
            One of ``"stability"``, ``"depth"``, ``"memory"``, ``"cpu"``,
            ``"ram"``, ``"disk"``.
        output_path : str or None
            If provided, save the ASCII chart to this file path.

        Returns
        -------
        str
            The ASCII chart as a string.
        """
        if not self._events:
            msg = "No telemetry events loaded. Call load_from_event_log() first."
            if output_path:
                with open(output_path, "w") as f:
                    f.write(msg + "\n")
            return msg

        # Extract values
        timeline_values: list[tuple[str, float]] = []
        for ev in self._events:
            val = self._get_metric_value(ev, metric)
            if val is not None:
                timeline_values.append((ev.timestamp, val))

        if not timeline_values:
            msg = f"No data for metric: {metric}"
            if output_path:
                with open(output_path, "w") as f:
                    f.write(msg + "\n")
            return msg

        # Determine chart width and scale
        terminal_width = 72
        chart_width = max(20, terminal_width - 24)
        max_val = max(v for _, v in timeline_values)
        min_val = min(v for _, v in timeline_values)
        val_range = max_val - min_val if max_val != min_val else 1.0

        # Bucket into rows (one per ~0.10 increment)
        num_rows = min(15, len(timeline_values))
        if num_rows < 1:
            num_rows = 1

        # Create buckets sorted by value descending
        buckets: list[tuple[float, float, list[tuple[str, float]]]] = []
        step_val = val_range / num_rows
        for i in range(num_rows):
            lower = max_val - (i + 1) * step_val
            upper = max_val - i * step_val
            bucket: list[tuple[str, float]] = [
                (ts, v) for ts, v in timeline_values if lower <= v < upper
            ]
            if bucket:
                label = f"{upper:.2f}"
                buckets.append((upper, lower, bucket))

        # Special handling for stability (0-1 range)
        if metric == "stability":
            # Show 0.90, 0.80, 0.70, ...
            buckets.clear()
            for level in range(10, -1, -1):
                lower = level / 10.0
                upper = (level + 1) / 10.0
                bucket = [
                    (ts, v)
                    for ts, v in timeline_values
                    if lower <= v < upper or (level == 10 and v == 1.0)
                ]
                if bucket or level < 9:
                    label = f"{upper:.2f}" if upper <= 1.0 else f"{level / 10.0:.2f}"
                    buckets.append(
                        (
                            upper if upper <= 1.0 else 1.0,
                            lower,
                            bucket,
                        )
                    )

        title = f"{metric.replace('_', ' ').title()} over {self._compute_time_span_str()}"

        lines: list[str] = []
        lines.append(title)
        lines.append("-" * len(title))

        for upper, lower, bucket in reversed(buckets):
            label = f"{upper:.2f}"
            count = len(bucket)
            bar_len = int((count / max(1, len(timeline_values))) * chart_width)
            bar = _ONION * bar_len
            lines.append(f"{label} {bar}")

        # Show some sample timestamps at bottom
        if len(timeline_values) > 0:
            first_ts = timeline_values[0][0]
            last_ts = timeline_values[-1][0]
            # Format timestamps to be shorter
            try:
                first_short = first_ts[:19].replace("T", " ")
                last_short = last_ts[:19].replace("T", " ")
            except IndexError:
                first_short = first_ts
                last_short = last_ts
            lines.append("")
            lines.append(f"  {first_short}  {'─' * (chart_width // 2 - 10)}  {last_short}")

        # Add count
        min_display = f"{min_val:.2f}"
        max_display = f"{max_val:.2f}"
        lines.append("")
        lines.append(f"  Range: {min_display} – {max_display}")
        lines.append(f"  Points: {len(timeline_values)}")

        result = "\n".join(lines)

        if output_path:
            with open(output_path, "w") as f:
                f.write(result + "\n")

        return result

    # ------------------------------------------------------------------
    # Anomaly Detection
    # ------------------------------------------------------------------

    def find_anomalies(
        self, time_range: Optional[tuple] = None
    ) -> list[dict[str, Any]]:
        """Detect anomalous telemetry events.

        Checks for:
          a) Sudden drops in cognitive stability score (>0.2 drop).
          b) Event storms (>100 events/min).
          c) Repeated recovery triggers.
          d) Planner depth spikes.
          e) Memory growth spikes.

        Parameters
        ----------
        time_range : (start, end) tuple or None
            Optional time window filter.

        Returns
        -------
        list[dict]
            Each dict has ``timestamp`` and ``description`` keys.
        """
        start_dt, end_dt = _parse_range_arg(time_range)

        filtered = self._events
        if start_dt is not None or end_dt is not None:
            filtered = [
                e
                for e in filtered
                if (start_dt is None or _parse_iso(e.timestamp) >= start_dt)
                and (end_dt is None or _parse_iso(e.timestamp) <= end_dt)
            ]

        anomalies: list[dict[str, Any]] = []

        if len(filtered) < 2:
            return anomalies

        # a) Sudden stability drops (>0.2 drop between consecutive events)
        for i in range(1, len(filtered)):
            prev = filtered[i - 1]
            curr = filtered[i]
            drop = prev.cognitive_stability_score - curr.cognitive_stability_score
            if drop > 0.2:
                anomalies.append(
                    {
                        "timestamp": curr.timestamp,
                        "description": (
                            f"Sudden stability drop: "
                            f"{prev.cognitive_stability_score:.3f} -> "
                            f"{curr.cognitive_stability_score:.3f} "
                            f"(drop of {drop:.3f})"
                        ),
                    }
                )

        # b) Event storms (>100 events/min)
        if len(filtered) >= 5:
            window_min = _parse_iso(filtered[0].timestamp)
            window_counts: list[tuple[str, int]] = []
            count = 0
            window_start = _parse_iso(filtered[0].timestamp)
            for ev in filtered:
                ts = _parse_iso(ev.timestamp)
                if (ts - window_start).total_seconds() <= 60:
                    count += 1
                else:
                    if count > 100:
                        window_counts.append((window_start.isoformat(), count))
                    window_start = ts
                    count = 1
            if count > 100:
                window_counts.append((window_start.isoformat(), count))
            for ts_str, cnt in window_counts:
                anomalies.append(
                    {
                        "timestamp": ts_str,
                        "description": (
                            f"Event storm: {cnt} events in a 60-second window"
                        ),
                    }
                )

        # c) Repeated recovery triggers (3+ recovery events in 5 minutes)
        recovery_timestamps: list[datetime] = []
        for ev in filtered:
            if "recover" in str(type(ev)).lower():
                # TelemetryData doesn't have event_type, so check raw events
                pass
        # Use raw events for recovery detection
        raw_filtered = self._raw_events
        if start_dt is not None or end_dt is not None:
            raw_filtered = [
                r
                for r in raw_filtered
                if (start_dt is None or _parse_iso(r.get("timestamp", "")) >= start_dt)
                and (end_dt is None or _parse_iso(r.get("timestamp", "")) <= end_dt)
            ]
        recovery_ts: list[datetime] = []
        for r in raw_filtered:
            if "recover" in r.get("event_type", "").lower():
                recovery_ts.append(_parse_iso(r.get("timestamp", "")))
        if len(recovery_ts) >= 3:
            for i in range(len(recovery_ts) - 2):
                if (
                    recovery_ts[i + 2] - recovery_ts[i]
                ).total_seconds() <= 300:
                    anomalies.append(
                        {
                            "timestamp": recovery_ts[i].isoformat(),
                            "description": (
                                f"Repeated recovery triggers: "
                                f"{len([t for t in recovery_ts if (t - recovery_ts[i]).total_seconds() <= 300])} "
                                f"recoveries within 5 minutes"
                            ),
                        }
                    )
                    break

        # d) Planner depth spikes (depth > 5)
        for ev in filtered:
            if ev.planner_depth > 5:
                anomalies.append(
                    {
                        "timestamp": ev.timestamp,
                        "description": (
                            f"Planner depth spike: depth={ev.planner_depth} "
                            f"(threshold: 5)"
                        ),
                    }
                )

        # e) Memory growth spikes (>500 entries between consecutive events)
        for i in range(1, len(filtered)):
            prev = filtered[i - 1]
            curr = filtered[i]
            growth = curr.memory_count - prev.memory_count
            if growth > 500:
                anomalies.append(
                    {
                        "timestamp": curr.timestamp,
                        "description": (
                            f"Memory growth spike: +{growth} entries "
                            f"({prev.memory_count} -> {curr.memory_count})"
                        ),
                    }
                )

        # Deduplicate by timestamp+description
        seen: set[tuple[str, str]] = set()
        unique_anomalies: list[dict[str, Any]] = []
        for a in anomalies:
            key = (a["timestamp"], a["description"])
            if key not in seen:
                seen.add(key)
                unique_anomalies.append(a)

        return unique_anomalies

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_report(self, path: str) -> None:
        """Export the full report as a JSON file.

        Parameters
        ----------
        path : str
            Destination file path.
        """
        report = self.generate_report()
        report_path = pathlib.Path(path).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_delta(delta) -> str:
        """Format a timedelta into a human-readable string."""
        total_s = int(delta.total_seconds())
        days, remainder = divmod(total_s, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")
        return "".join(parts)

    def _compute_time_span_str(self) -> str:
        """Return a human-readable time span over all loaded events."""
        if not self._events:
            return "no data"
        if len(self._events) == 1:
            return f"1 event at {self._events[0].timestamp}"
        first_ts = _parse_iso(self._events[0].timestamp)
        last_ts = _parse_iso(self._events[-1].timestamp)
        delta = last_ts - first_ts
        return self._format_delta(delta)

    @staticmethod
    def _get_metric_value(ev: TelemetryData, metric: str) -> Optional[float]:
        """Extract a numeric metric value from a TelemetryData instance."""
        metric_map = {
            "stability": ev.cognitive_stability_score,
            "depth": float(ev.planner_depth),
            "memory": float(ev.memory_count),
            "cpu": ev.cpu_load,
            "ram": ev.ram_percent,
            "disk": ev.disk_percent,
            "throughput": ev.event_throughput,
            "latency": ev.task_latency_avg,
            "recovery": ev.recovery_frequency,
            "goals": float(ev.active_goals),
            "threads": float(ev.thread_count),
            "health": ev.memory_health,
        }
        return metric_map.get(metric.lower())


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_replay_instance: Optional[TelemetryReplay] = None
_replay_instance_lock = threading.Lock()


def get_replay() -> TelemetryReplay:
    """Return the application-wide TelemetryReplay singleton."""
    global _replay_instance
    with _replay_instance_lock:
        if _replay_instance is None:
            _replay_instance = TelemetryReplay()
        return _replay_instance


def load_events(path: Optional[str] = None) -> int:
    """Convenience: load telemetry events from an NDJSON log.

    Parameters
    ----------
    path : str or None
        Path to the NDJSON log file.  Defaults to
        ``~/.hermes/core/data/agent_events.ndjson``.

    Returns
    -------
    int
        Number of telemetry events loaded.
    """
    replay = get_replay()
    return replay.load_from_event_log(path)
