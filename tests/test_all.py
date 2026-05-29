"""
Comprehensive test suite for Hermes Core Phase 2 changes.

Tests:
  1. LLM-driven planner decomposition
  2. 4 dormant module activation (telemetry, watchdog, drift, goals)
  3. Reflection engine UUID fix
  4. Module import sanity

Usage:
    cd ~/.hermes/core && python3 tests/test_all.py
"""

import json
import os
import sys
import time
import traceback

PASS = 0
FAIL = 0
SKIP = 0

# Ensure import paths
sys.path.insert(0, os.path.expanduser("~/.hermes/core"))
sys.path.insert(0, os.path.expanduser("~/.hermes/plugins/hermes-core"))


def note(msg):
    print(f"  INFO: {msg}")


def check(description, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {description}")
    else:
        FAIL += 1
        print(f"  ❌ {description}" + (f" — {detail}" if detail else ""))


def skip(description, reason):
    global SKIP
    SKIP += 1
    print(f"  ⏭  {description} (跳过: {reason})")


def run(test_fn):
    """Run a test function and print its header."""
    name = test_fn.__name__.replace("test_", "").replace("_", " ")
    print(f"\n─── {name} ───")
    try:
        test_fn()
    except Exception as exc:
        global FAIL
        FAIL += 1
        print(f"  ❌ UNHANDLED EXCEPTION: {exc}")
        traceback.print_exc()


# ── 1. Core/__init__.py ─────────────────────────────────

def test_core_init():
    """Verify the 4 new modules are accessible via sys.path"""
    # Check direct module-level singleton getters
    import drift_analyzer
    import goal_manager
    import telemetry
    import watchdog
    import runtime_integration

    check("drift_analyzer.get_drift_analyzer callable", callable(drift_analyzer.get_drift_analyzer))
    check("goal_manager.get_goal_manager callable", callable(goal_manager.get_goal_manager))
    check("telemetry.get_telemetry callable", callable(telemetry.get_telemetry))
    check("watchdog.get_watchdog callable", callable(watchdog.get_watchdog))
    check("runtime_integration.RuntimeHotPath callable", callable(runtime_integration.RuntimeHotPath))


# ── 2. Planner ─────────────────────────────────────────

def test_planner_import():
    from planner import get_planner, Plan, PlanStep
    p = get_planner()
    check("Planner singleton created", p is not None)
    check("Planner has _llm_decompose_goal", hasattr(p, "_llm_decompose_goal"))
    check("Planner has _decompose_goal (updated)", hasattr(p, "_decompose_goal"))
    check("Planner has plan()", hasattr(p, "plan"))
    check("Plan dataclass", Plan is not None)
    check("PlanStep dataclass", PlanStep is not None)


def test_planner_fallback_no_key():
    """Without API key, planner should fall back to patterns."""
    from planner import get_planner

    # Ensure no API key in env
    old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
    old_key2 = os.environ.pop("HERMES_LLM_API_KEY", None)
    try:
        p = get_planner()
        result = p.plan("测试目标", {"max_cost": "low"})
        check("fallback plan created without API key", result is not None)
        check("fallback plan has steps", len(result.steps) > 0)
        check("fallback plan has plan_id", bool(result.plan_id))
        check("fallback plan has risk_assessment", bool(result.risk_assessment))
    finally:
        if old_key:
            os.environ["DEEPSEEK_API_KEY"] = old_key
        if old_key2:
            os.environ["HERMES_LLM_API_KEY"] = old_key2


def test_planner_with_key():
    """With API key, planner should use LLM decomposition."""
    # Let the planner load .env first (handles provider priority correctly)
    from planner import _ensure_hermes_env
    _ensure_hermes_env()

    api_key = (
        os.environ.get("HERMES_LLM_API_KEY")
        or os.environ.get("XIAOMI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY", "")
    )

    if not api_key:
        skip("LLM decomposition (no API key available)", "No API key in .env")
        return

    from planner import get_planner
    p = get_planner()
    # Test that _llm_decompose_goal calls the API
    sub_goals = p._llm_decompose_goal("审计代码库，找出未处理的异常路径", {"max_cost": "low"})
    check("LLM decomposition returns list", isinstance(sub_goals, list))
    check("LLM decomposition has steps", len(sub_goals) > 0)
    for i, sg in enumerate(sub_goals):
        check(f"step[{i}] has description", bool(sg.get("description")))
        check(f"step[{i}] has domain", bool(sg.get("domain")))


# ── 3. Runtime Integration ────────────────────────────

def test_activation():
    """Verify RuntimeHotPath._init_subsystems() activates all 4 modules."""
    import runtime_integration
    rt = runtime_integration.RuntimeHotPath()
    rt._ensure_subsystems()

    # Telemetry
    check("telemetry instance exists", rt._telemetry is not None)
    if rt._telemetry:
        running = getattr(rt._telemetry, "_running", False)
        check("telemetry daemon running", running is True)

    # Watchdog
    check("watchdog instance exists", rt._watchdog is not None)
    if rt._watchdog:
        t = getattr(rt._watchdog, "_monitor_thread", None)
        alive = t.is_alive() if t else False
        check("watchdog thread alive", alive is True)

    # Drift
    check("drift instance exists", rt._drift is not None)

    # Goal Manager
    check("goals instance exists", rt._goals is not None)


# ── 4. Goal Manager ─────────────────────────────────

def test_goal_manager():
    """Goal manager lifecycle: register → complete → status."""
    import goal_manager as _gm
    from goal_manager import get_goal_manager as get_gm

    gm = get_gm()
    check("GoalManager singleton", gm is not None)
    check("GoalManager.register_goal has method", hasattr(gm, "register_goal"))
    check("GoalManager.complete_goal has method", hasattr(gm, "complete_goal"))

    gid = gm.register_goal("test goal: unit test", priority=3)
    check("goal registered with id", gid is not None)
    check("goal id is string", isinstance(gid, str))
    check("goal id not empty", len(gid) > 0)

    gm.complete_goal(gid)
    check("goal completed without error", True)


# ── 5. Drift Analyzer ───────────────────────────────

def test_drift_analyzer():
    """Drift analyzer instantiates and reports."""
    from drift_analyzer import get_drift_analyzer

    da = get_drift_analyzer()
    check("drift analyzer singleton", da is not None)

    summary = da.get_summary()
    check("drift summary is dict", isinstance(summary, dict))
    check("drift summary has status key", "status" in summary)


# ── 6. Watchdog ─────────────────────────────────────

def test_watchdog():
    """Watchdog heartbeat and alert tracking."""
    from watchdog import get_watchdog

    wd = get_watchdog()
    check("watchdog singleton", wd is not None)
    check("watchdog has heartbeat", hasattr(wd, "heartbeat"))
    check("watchdog has get_alerts", hasattr(wd, "get_alerts"))

    wd.heartbeat("test_tool")
    check("heartbeat sent without error", True)


# ── 7. Telemetry ───────────────────────────────────

def test_telemetry():
    """Telemetry runs and collects metrics."""
    from telemetry import get_telemetry

    tel = get_telemetry()
    check("telemetry singleton", tel is not None)

    status = tel.get_status() if hasattr(tel, "get_status") else None
    check("telemetry get_status exists or returns", status is None or isinstance(status, dict))

    # Verify collection loop is running
    running = getattr(tel, "_running", False)
    check("telemetry _running", running is True)


# ── 8. Reflection UUID fix ─────────────────────────

def test_reflection_uuid():
    """_row_to_reflection reads reflection_id directly."""
    import reflection_engine as re

    class MockRow:
        def __getitem__(self, key):
            data = {
                "reflection_id": "refl_test_abcdef",
                "session_id": "session_x",
                "task_id": "task_y",
                "task_description": "test",
                "goal": "test",
                "result_summary": "ok",
                "success": 1,
                "mistakes": "[]",
                "improvements": "[]",
                "successful_patterns": "[]",
                "created_at": "2026-01-01T00:00:00",
            }
            return data.get(key, "")

    ref = re.ReflectionEngine._row_to_reflection(MockRow())
    check("reflection_id is string", isinstance(ref.reflection_id, str))
    check("reflection_id matches db", ref.reflection_id == "refl_test_abcdef")
    check("task_id preserved", ref.task_id == "task_y")
    check("session_id preserved", ref.session_id == "session_x")


# ── 9. EventBus callback fix ──────────────────────

def test_eventbus_callback_type():
    """EventBus callbacks accept Event objects (not dict)."""
    from event_bus import Event, EventBus

    bus = EventBus()

    received = []

    def test_callback(ev):
        received.append(ev.data.get("test_key", None))

    bus.subscribe("test.event", test_callback)
    bus.publish("test.event", {"test_key": "hello_world"})

    check("callback received data", len(received) > 0)
    check("callback got .data.get() value", received[0] == "hello_world")


# ── 10. Semantic Retrieval ──────────────────────

def test_semantic_retrieval():
    """SemanticRetrieval TF-IDF index, search, and category filter."""
    from semantic_retrieval import SemanticRetrieval

    sr = SemanticRetrieval()
    sr.__class__._instance = None
    sr2 = SemanticRetrieval()

    sr2.index_memories([
        {"memory_id": "m1", "text": "Python GIL thread safety", "category": "tech"},
        {"memory_id": "m2", "text": "SQLite WAL mode concurrent reads", "category": "db"},
        {"memory_id": "m3", "text": "Python asyncio event loop", "category": "tech"},
    ])
    check("TF-IDF index built", sr2._total_docs == 3)

    results = sr2.search("python thread", top_k=2)
    check("search returns results", len(results) > 0)
    check("search has score", "score" in results[0] if results else False)

    cat_results = sr2.search_by_category("python", category="tech", top_k=5)
    check("category filter works", len(cat_results) > 0)
    check("category filter correct", all(r["category"] == "tech" for r in cat_results))


def test_memory_semantic_integration():
    """MemoryManager.semantic_search() integration."""
    import tempfile, shutil
    from pathlib import Path
    from db_schema import SchemaManager
    import db_schema as _db_schema_mod

    tmpdir = tempfile.mkdtemp()
    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    _db_schema_mod.DATABASE_SCHEMAS['memory'] = {
        'episodic_memories': '''CREATE TABLE IF NOT EXISTS episodic_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            description TEXT NOT NULL, summary TEXT, outcome TEXT, tags TEXT,
            created_at TEXT NOT NULL, access_count INTEGER DEFAULT 0)''',
        'semantic_facts': '''CREATE TABLE IF NOT EXISTS semantic_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fact TEXT NOT NULL,
            category TEXT DEFAULT 'general', confidence REAL DEFAULT 1.0,
            source TEXT, created_at TEXT NOT NULL, last_accessed_at TEXT,
            access_count INTEGER DEFAULT 0)''',
        'procedural_memories': '''CREATE TABLE IF NOT EXISTS procedural_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, skill_name TEXT NOT NULL,
            trigger_conditions TEXT, steps TEXT, domain TEXT,
            success_count INTEGER DEFAULT 0, fail_count INTEGER DEFAULT 0,
            avg_duration_s REAL DEFAULT 0.0, last_used_at TEXT)''',
        'environment_facts': '''CREATE TABLE IF NOT EXISTS environment_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL,
            value TEXT, category TEXT DEFAULT 'system',
            last_verified_at TEXT, source TEXT)''',
    }

    sm = SchemaManager(data_dir)
    sm.initialize("memory")
    conn = sm.get_connection("memory")
    conn.execute("INSERT INTO episodic_memories (session_id, description, summary, outcome, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                 ("s1", "Implemented search module", "Search done", "Success"))
    conn.execute("INSERT INTO semantic_facts (fact, category, confidence, created_at) VALUES (?, ?, ?, datetime('now'))",
                 ("Python uses GIL", "programming", 0.9))
    conn.commit()

    from memory_manager import MemoryManager
    MemoryManager._instance = None  # reset singleton for test isolation
    mm = MemoryManager(data_dir)

    count = mm.build_semantic_index()
    check("build_semantic_index counts", count == 2)

    results = mm.semantic_search("search module", top_k=3)
    check("semantic_search returns results", len(results) > 0)

    all_results = mm.search_all("python")
    check("search_all has semantic_tfidf", "semantic_tfidf" in all_results)

    shutil.rmtree(tmpdir, ignore_errors=True)


# ── Runner ──────────────────────────────────────────

def main():
    global PASS, FAIL, SKIP
    print("=" * 60)
    print(" Hermes Core — Phase 2 Test Suite")
    print("=" * 60)

    tests = [
        test_core_init,
        test_planner_import,
        test_planner_fallback_no_key,
        test_planner_with_key,
        test_activation,
        test_goal_manager,
        test_drift_analyzer,
        test_watchdog,
        test_telemetry,
        test_reflection_uuid,
        test_eventbus_callback_type,
        test_semantic_retrieval,
        test_memory_semantic_integration,
    ]

    for t in tests:
        run(t)

    print()
    print("=" * 60)
    total = PASS + FAIL + SKIP
    print(f"  Results:  {PASS} passed  {FAIL} failed  {SKIP} skipped  (total: {total})")
    print(f"  Score:    {PASS}/{total - SKIP} active tests passed")
    print("=" * 60)

    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
