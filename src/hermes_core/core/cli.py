#!/usr/bin/env python3
"""
Hermes Core CLI — command-line interface for managing the Hermes Core runtime.

Usage:
    hermes-core init              Initialize all subsystems
    hermes-core status            Show comprehensive system status
    hermes-core health            Run health check
    hermes-core snapshot          Capture a world state snapshot
    hermes-core observe           Run one self-observation cycle
    hermes-core supervisor start  Start the runtime supervisor monitor
    hermes-core supervisor stop   Stop the runtime supervisor
    hermes-core supervisor status Show supervisor status
    hermes-core event log <type>  Log an event
    hermes-core event recent [n]  Show recent events
    hermes-core event stats       Show event log statistics
    hermes-core memory stats      Show memory system statistics
    hermes-core memory search <q> Search across all memory layers
    hermes-core reflect <task_id> Generate reflection for a task
    hermes-core experience stats  Show experience system statistics
    hermes-core tools list        List registered tools
    hermes-core tools find <q>    Find tools by capability
    hermes-core policy check      Check policy configuration
    hermes-core graph create ...  Create a task graph (interactive prompt)
    hermes-core recover list      List recoverable tasks
    hermes-core version           Show version
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

CORE_DIR = Path(__file__).parent.resolve()
DATA_DIR = CORE_DIR / "data"
CONFIG_DIR = CORE_DIR / "config"

# Ensure core directory is on path for standalone usage
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))


def _ensure_init() -> None:
    """Lazy-import the kernel and initialize if needed."""
    from kernel import get_kernel

    k = get_kernel()
    if not k.is_initialized():
        print("🔄 Initializing Hermes Core subsystems...")
        result = k.initialize()
        print(f"✅ Initialization complete ({len(result.get('subsystems', []))} subsystems)")
    return k


def cmd_init(args: List[str]) -> None:
    """Initialize all subsystems."""
    k = _ensure_init()
    status = k.get_status()
    print(f"Hermes Core v{status['kernel']['version']}")
    print(f"  Uptime: {status['kernel']['uptime_s']:.0f}s")
    print(f"  Subsystems: {len(status) - 1} active")
    for name, sub in status.items():
        if name == "kernel":
            continue
        ok = sub.get("healthy") if isinstance(sub, dict) else True
        print(f"    {'✅' if ok else '❌'} {name}")


def cmd_status(args: List[str]) -> None:
    """Show comprehensive system status."""
    k = _ensure_init()
    status = k.get_status()
    print(json.dumps(status, indent=2, default=str))


def cmd_health(args: List[str]) -> None:
    """Run health check."""
    k = _ensure_init()
    health = k.health_check()
    print(f"System Health: {'✅ HEALTHY' if health.get('healthy', False) else '❌ ISSUES FOUND'}")
    for subsystem, result in health.items():
        if subsystem == "healthy":
            continue
        if isinstance(result, dict):
            ok = result.get("healthy", result.get("ok", True))
            print(f"  {'✅' if ok else '❌'} {subsystem}: {result.get('message', 'ok')}")
        else:
            print(f"  {'✅' if result else '❌'} {subsystem}")


def cmd_snapshot(args: List[str]) -> None:
    """Capture a world state snapshot."""
    from state_manager import get_state_manager

    sm = get_state_manager()
    sid = sm.capture_state(description="CLI snapshot")
    state = sm.restore_state(sid)
    print(f"📸 Snapshot captured: {sid}")
    print(f"  CPU load: {state.get('system_state', {}).get('cpu', {}).get('load_1m', '?')}")
    print(f"  RAM: {state.get('system_state', {}).get('memory', {}).get('available_mb', '?')} MB avail")
    print(f"  Disk: {state.get('system_state', {}).get('disk', {}).get('percent', '?')}% used")
    print(f"  Network: {state.get('system_state', {}).get('network', {}).get('status', '?')}")


def cmd_observe(args: List[str]) -> None:
    """Run one self-observation cycle."""
    from self_observation import get_observer

    obs = get_observer()
    report = obs.run_once()
    print(f"🔍 Observation Report ({report.observation_id[:8]}...)")
    print(f"  System healthy: {'✅' if report.system_healthy else '❌'}")
    print(f"  Warnings ({len(report.warnings)}):")
    for w in report.warnings[:5]:
        print(f"    ⚠ {w}")
    if report.warnings and len(report.warnings) > 5:
        print(f"    ... and {len(report.warnings) - 5} more")
    print(f"  Recommendations ({len(report.recommendations)}):")
    for r in report.recommendations[:3]:
        print(f"    💡 {r}")
    if report.auto_actions_taken:
        print(f"  Auto-actions taken: {len(report.auto_actions_taken)}")


def cmd_supervisor(args: List[str]) -> None:
    """Manage the runtime supervisor."""
    from runtime_supervisor import get_supervisor

    sup = get_supervisor()
    if args.supervisor_cmd == "start":
        sup.start()
        print("✅ Runtime supervisor started (monitoring every 30s)")
    elif args.supervisor_cmd == "stop":
        sup.stop()
        print("⏹ Runtime supervisor stopped")
    elif args.supervisor_cmd == "status":
        status = sup.get_status()
        print(f"Runtime Supervisor:")
        print(f"  Running: {status.get('running', False)}")
        print(f"  Healthy: {'✅' if status.get('healthy') else '❌'}")
        print(f"  CPU load: {status.get('cpu_load', '?')}")
        print(f"  RAM: {status.get('ram_percent', '?')}% used")
        print(f"  Disk: {status.get('disk_percent', '?')}% used")
        print(f"  Browsers: {status.get('browser_count', 0)}")
        print(f"  Tasks: {status.get('task_count', 0)}")
        alerts = status.get('alerts', [])
        if alerts:
            print(f"  Alerts ({len(alerts)}):")
            for a in alerts:
                print(f"    ⚠ {a}")


def cmd_event(args: List[str]) -> None:
    """Event log management."""
    from event_logger import get_logger

    logger = get_logger()
    if args.event_cmd == "log":
        data = {"message": " ".join(args.event_args) if args.event_args else "manual event"}
        eid = logger.log("cli.event", data)
        print(f"📝 Event logged: {eid}")
    elif args.event_cmd == "recent":
        n = int(args.event_args[0]) if args.event_args else 10
        events = logger.latest(n=n)
        print(f"Recent events (last {len(events)}):")
        for e in events[:10]:
            print(f"  [{e.get('severity', 'info').upper():8}] {e.get('event_type','?'):30} {str(e.get('data',{}))[:60]}")
    elif args.event_cmd == "stats":
        stats = logger.get_stats()
        print(f"Event Log Stats:")
        print(f"  Total events: {stats.get('total_events', 0)}")
        print(f"  Severity: {stats.get('by_severity', {})}")
        print(f"  File size: {stats.get('file_size', 0)} bytes")
        top = stats.get('top_event_types', {})
        if top:
            print(f"  Top event types: {dict(sorted(top.items(), key=lambda x: -x[1])[:5])}")


def cmd_memory(args: List[str]) -> None:
    """Memory system management."""
    from memory_manager import get_memory_manager

    mm = get_memory_manager()
    if args.memory_cmd == "stats":
        stats = mm.get_stats()
        print(f"Memory System Stats:")
        for layer, info in stats.items():
            print(f"  {layer}: {info}")
    elif args.memory_cmd == "search":
        query = " ".join(args.memory_args) if args.memory_args else ""
        results = mm.search_all(query)
        print(f"Search '{query}':")
        for mem_type, items in results.items():
            if items:
                print(f"  {mem_type} ({len(items)}):")
                for item in items[:3]:
                    print(f"    {str(item)[:100]}")


def cmd_reflect(args: List[str]) -> None:
    """Generate reflection for a task."""
    from reflection_engine import get_reflection_engine

    refl = get_reflection_engine()
    task_id = args.task_id
    reflection = refl.get_reflection(task_id)
    if reflection:
        print(f"Reflection for task {task_id}:")
        print(f"  Success: {reflection.success}")
        print(f"  Mistakes: {reflection.mistakes}")
        print(f"  Improvements: {reflection.improvements}")
        print(f"  Patterns: {reflection.successful_patterns}")
    else:
        print(f"No reflection found for task {task_id}")


def cmd_experience(args: List[str]) -> None:
    """Experience system management."""
    from experience_manager import get_experience

    exp = get_experience()
    summary = exp.get_summary()
    print(f"Experience System Summary:")
    for key, val in summary.items():
        print(f"  {key}: {val}")


def cmd_tools(args: List[str]) -> None:
    """Tool registry management."""
    from tool_registry import get_registry

    reg = get_registry()
    if args.tools_cmd == "list":
        tools = reg.list_all()
        print(f"Registered tools ({len(tools)}):")
        for t in tools:
            print(f"  {t.name:25} risk={t.risk:7} cost={t.cost:6} calls={t.call_count:4} rate={t.success_rate:.0%}")
    elif args.tools_cmd == "find":
        query = " ".join(args.tools_args) if args.tools_args else ""
        results = reg.find(query)
        print(f"Tools matching '{query}' ({len(results)}):")
        for t in results:
            print(f"  {t.name:25} {t.description[:60]}")


def cmd_policy(args: List[str]) -> None:
    """Policy engine management."""
    from policy_engine import get_policy_engine

    pe = get_policy_engine()
    summary = pe.get_summary()
    print(f"Policy Engine Summary:")
    print(json.dumps(summary, indent=2, default=str))


def cmd_recover(args: List[str]) -> None:
    """Recovery management."""
    from recovery_manager import get_recovery_manager

    rm = get_recovery_manager()
    if args.recover_cmd == "list":
        recoverable = rm.list_recoverable()
        print(f"Recoverable tasks ({len(recoverable)}):")
        for t in recoverable[:10]:
            print(f"  {t.get('task_id', '?'):15} {str(t.get('description', ''))[:60]}")
    else:
        plan = rm.get_recovery_plan()
        print(f"Recovery Plan:")
        print(json.dumps(plan, indent=2, default=str))


def cmd_version(args: List[str]) -> None:
    """Show version."""
    print(f"Hermes Core v0.1.0")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes Core — Agent Operating System CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialize all subsystems")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="Show comprehensive status")
    p_status.set_defaults(func=cmd_status)

    p_health = sub.add_parser("health", help="Run health check")
    p_health.set_defaults(func=cmd_health)

    p_snap = sub.add_parser("snapshot", help="Capture world state snapshot")
    p_snap.set_defaults(func=cmd_snapshot)

    p_obs = sub.add_parser("observe", help="Run one observation cycle")
    p_obs.set_defaults(func=cmd_observe)

    p_sup = sub.add_parser("supervisor", help="Manage runtime supervisor")
    p_sup.add_argument("supervisor_cmd", choices=["start", "stop", "status"])
    p_sup.set_defaults(func=cmd_supervisor)

    p_evt = sub.add_parser("event", help="Manage event log")
    p_evt.add_argument("event_cmd", choices=["log", "recent", "stats"])
    p_evt.add_argument("event_args", nargs="*", help="Arguments")
    p_evt.set_defaults(func=cmd_event)

    p_mem = sub.add_parser("memory", help="Memory system")
    p_mem.add_argument("memory_cmd", choices=["stats", "search"])
    p_mem.add_argument("memory_args", nargs="*", help="Search query")
    p_mem.set_defaults(func=cmd_memory)

    p_ref = sub.add_parser("reflect", help="Reflect on a task")
    p_ref.add_argument("task_id", help="Task ID")
    p_ref.set_defaults(func=cmd_reflect)

    p_exp = sub.add_parser("experience", help="Experience system")
    p_exp.add_argument("experience_cmd", choices=["stats"])
    p_exp.set_defaults(func=cmd_experience)

    p_tools = sub.add_parser("tools", help="Tool registry")
    p_tools.add_argument("tools_cmd", choices=["list", "find"])
    p_tools.add_argument("tools_args", nargs="*", help="Search query")
    p_tools.set_defaults(func=cmd_tools)

    p_pol = sub.add_parser("policy", help="Policy engine")
    p_pol.add_argument("policy_cmd", choices=["check"], nargs="?", default="check")
    p_pol.set_defaults(func=cmd_policy)

    p_rec = sub.add_parser("recover", help="Recovery management")
    p_rec.add_argument("recover_cmd", choices=["list", "plan"], nargs="?", default="list")
    p_rec.set_defaults(func=cmd_recover)

    p_ver = sub.add_parser("version", help="Show version")
    p_ver.set_defaults(func=cmd_version)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
