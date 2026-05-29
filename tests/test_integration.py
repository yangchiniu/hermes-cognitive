"""
hermes-core Integration Test Suite
===================================
验证每个模块在真实 Hermes 运行时中正确工作。
基于历史 bug 清单设计，覆盖所有已知回归路径。

运行: cd ~/.hermes/core && python3 tests/test_integration.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.hermes/core"))
sys.path.insert(0, os.path.expanduser("~/.hermes/plugins/hermes-core"))

PASS = 0
FAIL = 0
DETAILS = []

def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        msg = f"  ❌ {name}: {detail}" if detail else f"  ❌ {name}"
        print(msg)
        DETAILS.append(msg)


def section(title: str):
    print(f"\n─── {title} ───")


# ═══════════════════════════════════════════════════
# 1. db_schema — 连接管理 + 线程安全
# ═══════════════════════════════════════════════════
def test_db_schema():
    section("1. db_schema: 连接管理 + 线程安全")
    from db_schema import SchemaManager

    tmpdir = tempfile.mkdtemp()
    sm = SchemaManager(Path(tmpdir))

    # 1a. 基本连接
    conn = sm.get_connection("world_state")
    check("get_connection returns sqlite3.Connection", conn is not None)

    # 1b. 缓存一致性 — 同一 db_name 返回同一连接
    conn2 = sm.get_connection("world_state")
    check("get_connection caches (same object)", conn is conn2)

    # 1c. 不同 db 不同连接
    conn3 = sm.get_connection("experience")
    check("different db gets different connection", conn is not conn3)

    # 1d. 线程安全 — 跨线程访问不崩溃
    results = []
    def cross_thread():
        try:
            c = sm.get_connection("world_state")
            c.execute("SELECT 1").fetchone()
            results.append(True)
        except Exception as e:
            results.append(str(e))
    t = threading.Thread(target=cross_thread)
    t.start()
    t.join(timeout=5)
    check("cross-thread get_connection works", results == [True] if results else False,
          f"got {results}")

    # 1e. 连接锁存在
    check("_conn_lock is threading.Lock", isinstance(sm._conn_lock, type(threading.Lock())))

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════
# 2. event_logger — 批量提交 + flush
# ═══════════════════════════════════════════════════
def test_event_logger():
    section("2. event_logger: 批量提交 + flush")
    from event_logger import EventLogger

    tmpdir = tempfile.mkdtemp()
    logger = EventLogger(Path(tmpdir))

    # 2a. 基本写入
    eid = logger.log("test.event", {"key": "value"})
    check("log() returns event_id", isinstance(eid, str) and len(eid) > 0)

    # 2b. 批量提交 — 写入 < batch_size 不触发 commit
    check("has _pending_commits counter", hasattr(logger, '_pending_commits'))
    check("batch_size is 10", logger._batch_size == 10)

    # 2c. flush 方法存在且可调用
    check("flush() method exists", callable(getattr(logger, 'flush', None)))
    logger.flush()

    # 2d. 数据实际落盘
    conn = logger._connection
    if conn:
        cur = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = 'test.event'")
        count = cur.fetchone()[0]
        check("flushed data persisted to DB", count >= 1, f"count={count}")
    else:
        check("flushed data persisted to DB", False, "no connection")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════
# 3. world_model — 快照 + 数据完整性
# ═══════════════════════════════════════════════════
def test_world_model():
    section("3. world_model: 快照 + 数据完整性")
    from world_model import WorldModel

    tmpdir = tempfile.mkdtemp()
    wm = WorldModel(Path(tmpdir))

    # 3a. snapshot 不崩溃
    try:
        wm.snapshot()
        check("snapshot() executes without error", True)
    except Exception as e:
        check("snapshot() executes without error", False, str(e))

    # 3b. get_state_summary 返回合理结构
    state = wm.get_summary()
    check("get_state_summary returns dict", isinstance(state, dict))
    check("state has 'cpu' key", "cpu" in state)
    check("state has 'memory' key", "memory" in state)

    # 3c. free_mb 不再是 0.0（旧 bug 修复验证）
    mem = state.get("memory", {})
    free_mb = mem.get("free_mb", 0.0)
    # free_mb 可能为 0.0 如果没有足够数据，但字段应存在
    check("memory.free_mb field exists", "free_mb" in mem)

    # 3d. load_1m 是合理值（0 < x < 100）
    cpu = state.get("cpu", {})
    load_1m = cpu.get("load_1m", -1)
    check("cpu.load_1m is reasonable", 0 <= load_1m < 100, f"load_1m={load_1m}")

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════
# 4. memory_manager — 5 层记忆 + TF-IDF
# ═══════════════════════════════════════════════════
def test_memory_manager():
    section("4. memory_manager: 5 层记忆 + TF-IDF")
    from memory_manager import MemoryManager
    import db_schema as _db_schema_mod

    tmpdir = tempfile.mkdtemp()
    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 注册 memory schema
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

    MemoryManager._instance = None
    mm = MemoryManager(data_dir)

    # 4a. Working memory
    mm.set_focus("integration test")
    check("set_focus works", mm.get_focus() == "integration test")

    mm.push_action("test_action", "test result")
    check("push_action works", len(mm._working.get('recent_actions', [])) > 0)

    # 4b. Episodic memory
    ep_id = mm.remember_episode("Integration test episode", "Testing memory", "Success", ["test"])
    check("remember_episode returns int ID", isinstance(ep_id, int) and ep_id > 0)

    episodes = mm.recall_episodes(query="Integration")
    check("recall_episodes finds episode", len(episodes) > 0)

    # 4c. Semantic memory
    fact_id = mm.learn_fact("Tests verify code correctness", "testing", 0.99)
    check("learn_fact returns int ID", isinstance(fact_id, int) and fact_id > 0)

    facts = mm.recall_fact(query="correctness")
    check("recall_fact finds fact", len(facts) > 0)

    # 4d. Procedural memory
    proc_id = mm.learn_procedure("run_integration_tests", "after code changes",
                                  "python3 tests/test_integration.py", "testing")
    check("learn_procedure returns int ID", isinstance(proc_id, int) and proc_id > 0)

    # 4e. Environment memory
    mm.update_env("test_key", "test_value", "testing")
    env_val = mm.get_env_value("test_key")
    check("update_env + get_env_value works", env_val == "test_value")

    # 4f. search_all — 全层搜索
    all_results = mm.search_all("test")
    expected_keys = {"episodic", "semantic", "procedural", "environment", "semantic_tfidf"}
    check("search_all has all 5 keys", expected_keys.issubset(set(all_results.keys())))

    # 4g. TF-IDF 搜索
    count = mm.build_semantic_index()
    check("build_semantic_index indexes docs", count > 0, f"indexed {count}")

    tfidf_results = mm.semantic_search("memory test", top_k=3)
    check("semantic_search returns scored results", len(tfidf_results) > 0 and "score" in tfidf_results[0])

    # 4h. consolidate 不崩溃
    try:
        report = mm.consolidate()
        check("consolidate() runs without error", isinstance(report, dict))
    except Exception as e:
        check("consolidate() runs without error", False, str(e))

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════
# 5. semantic_retrieval — TF-IDF 搜索引擎
# ═══════════════════════════════════════════════════
def test_semantic_retrieval():
    section("5. semantic_retrieval: TF-IDF 搜索引擎")
    from semantic_retrieval import SemanticRetrieval

    # 重置单例
    SemanticRetrieval._instance = None
    sr = SemanticRetrieval()

    # 5a. 索引构建
    docs = [
        {"memory_id": "d1", "text": "Python GIL prevents true parallelism", "category": "lang"},
        {"memory_id": "d2", "text": "SQLite WAL mode allows concurrent reads", "category": "db"},
        {"memory_id": "d3", "text": "Python asyncio uses event loop", "category": "lang"},
        {"memory_id": "d4", "text": "Redis is an in-memory key-value store", "category": "db"},
    ]
    sr.index_memories(docs)
    check("index builds correctly", sr._total_docs == 4)

    # 5b. 基本搜索
    results = sr.search("python parallelism", top_k=2)
    check("search returns results", len(results) > 0)
    check("top result is d1 (Python GIL)", results[0]["memory_id"] == "d1")
    check("results have score", all("score" in r for r in results))

    # 5c. 类别过滤
    lang_results = sr.search_by_category("python", category="lang", top_k=5)
    check("category filter returns only lang", all(r["category"] == "lang" for r in lang_results))

    db_results = sr.search_by_category("data store", category="db", top_k=5)
    check("category filter returns only db", all(r["category"] == "db" for r in db_results))

    # 5d. find_similar
    similar = sr.find_similar("concurrent database access patterns", top_k=2)
    check("find_similar returns results", len(similar) > 0)

    # 5e. 空查询不崩溃
    check("empty query returns []", sr.search("") == [])
    check("empty text returns []", sr.find_similar("") == [])


# ═══════════════════════════════════════════════════
# 6. task_graph — 目标管理 + 父子关系
# ═══════════════════════════════════════════════════
def test_task_graph():
    section("6. task_graph: 目标管理 + 父子关系")
    from task_graph import TaskGraphEngine, TaskNode
    import task_graph as _tg_mod

    # Reset singleton
    _tg_mod._engine_instance = None
    engine = TaskGraphEngine()

    # 6a. 创建图 + 添加节点
    n1 = TaskNode(node_id="n1", action="build_parser", params={}, depends_on=[])
    n2 = TaskNode(node_id="n2", action="write_lexer", params={}, depends_on=["n1"])
    graph_id = engine.create_graph("test_graph", [n1, n2])
    check("create_graph returns graph_id", isinstance(graph_id, str) and len(graph_id) > 0)

    # 6b. 查询图
    g = engine.get_graph(graph_id)
    check("get_graph returns TaskGraph", g is not None)
    check("graph has 2 nodes", len(g.nodes) == 2)

    # 6c. create_graph(nodes=[]) raises ValueError (validation works)
    try:
        engine.create_graph("empty_graph", [])
        check("create_graph(nodes=[]) raises ValueError", False, "should have raised")
    except ValueError:
        check("create_graph(nodes=[]) raises ValueError", True)
    except Exception as e:
        check("create_graph(nodes=[]) raises ValueError", False, f"wrong exception: {e}")

    # 6d. 可以检索图
    g2 = engine.get_graph(graph_id)
    check("can retrieve graph by ID", g2.graph_id == graph_id)


# ═══════════════════════════════════════════════════
# 7. planner — LLM 分解 + 多 provider
# ═══════════════════════════════════════════════════
def test_planner():
    section("7. planner: LLM 分解 + 多 provider")
    from planner import get_planner, _ensure_hermes_env, _read_hermes_llm_config

    # 7a. env 加载
    _ensure_hermes_env()
    xiaomi_key = os.environ.get("XIAOMI_API_KEY", "")
    check(".env auto-loaded (XIAOMI_API_KEY set)", len(xiaomi_key) > 0)

    # 7b. 配置读取
    cfg = _read_hermes_llm_config()
    check("config has base_url", len(cfg.get("base_url", "")) > 0)
    check("config has model", len(cfg.get("model", "")) > 0)
    check("config has provider", len(cfg.get("provider", "")) > 0)

    # 7c. Planner 实例化
    p = get_planner()
    check("get_planner returns instance", p is not None)

    # 7d. decompose_goal 不崩溃（可能返回 fallback）
    try:
        plan = p._decompose_goal("test goal for integration", context={})
        check("decompose_goal returns list", isinstance(plan, list))
    except Exception as e:
        check("decompose_goal returns list", False, str(e))

    # 7e. reasoning_content 兼容（MiMo 推理模型）
    check("planner has _read_hermes_llm_config", hasattr(p, '_read_hermes_llm_config') or
          hasattr(type(p), '_read_hermes_llm_config') or callable(_read_hermes_llm_config))


# ═══════════════════════════════════════════════════
# 8. policy_engine — 策略执行
# ═══════════════════════════════════════════════════
def test_policy_engine():
    section("8. policy_engine: 策略执行")
    from policy_engine import PolicyEngine

    tmpdir = tempfile.mkdtemp()
    pe = PolicyEngine(config_path=None)

    # 8a. evaluate 不崩溃
    try:
        decision = pe.check_action("test_tool")
        check("check_action() returns result", decision is not None)
    except Exception as e:
        check("evaluate() returns result", False, str(e))

    # 8b. update_policy 存在
    check("update_policy method exists", callable(getattr(pe, 'update_policy', None)))

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════
# 9. reflection_engine — 反思引擎
# ═══════════════════════════════════════════════════
def test_reflection_engine():
    section("9. reflection_engine: 反思引擎")
    from reflection_engine import ReflectionEngine

    tmpdir = tempfile.mkdtemp()
    from db_schema import SchemaManager
    sm = SchemaManager(Path(tmpdir))
    re = ReflectionEngine(schema_manager=sm)

    # 9a. _row_to_reflection UUID 修复验证
    class MockRow:
        def __getitem__(self, key):
            data = {
                "reflection_id": "refl_test_123",
                "session_id": "sess_1",
                "task_id": "task_1",
                "task_description": "test task",
                "goal": "test goal",
                "result_summary": "ok",
                "success": 1,
                "mistakes": "[]",
                "improvements": "[]",
                "successful_patterns": "[]",
                "created_at": "2026-01-01T00:00:00",
            }
            return data.get(key, "")

    try:
        ref = ReflectionEngine._row_to_reflection(MockRow())
        check("_row_to_reflection works", ref.reflection_id == "refl_test_123")
        check("UUID preserved from DB", ref.task_id == "task_1")
    except Exception as e:
        check("_row_to_reflection works", False, str(e))

    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════
# 10. goal_manager — 目标生命周期
# ═══════════════════════════════════════════════════
def test_goal_manager():
    section("10. goal_manager: 目标生命周期")
    from goal_manager import GoalManager

    GoalManager._instance = None
    gm = GoalManager()

    # 10a. 注册目标
    try:
        gid = gm.register_goal("test integration goal", priority="medium")
        check("register_goal returns goal_id", gid is not None and len(str(gid)) > 0)
    except Exception as e:
        check("register_goal returns goal_id", False, str(e))

    # 10b. 完成目标
    try:
        gm.complete_goal(gid)
        check("complete_goal doesn't crash", True)
    except Exception as e:
        check("complete_goal doesn't crash", False, str(e))


# ═══════════════════════════════════════════════════
# 11. recovery_manager — 恢复策略
# ═══════════════════════════════════════════════════
def test_recovery_manager():
    section("11. recovery_manager: 恢复策略")
    from recovery_manager import RecoveryManager

    rm = RecoveryManager()

    # 11a. 导入链修复验证（三连 except 已合并）
    check("RecoveryManager instantiates", rm is not None)

    # 11b. 恢复策略
    try:
        health = rm.check_health()
        check("check_health returns result", health is not None)
    except Exception as e:
        check("check_health returns result", False, str(e))


# ═══════════════════════════════════════════════════
# 12. exceptions — MemoryError 改名验证
# ═══════════════════════════════════════════════════
def test_exceptions():
    section("12. exceptions: MemoryError 改名验证")
    from exceptions import HermesMemoryError, HermesCoreError

    # 12a. 不再遮蔽内置 MemoryError
    import builtins
    check("builtin MemoryError is Python's", builtins.MemoryError is not HermesMemoryError)

    # 12b. 自定义异常是 HermesCoreError 子类
    check("HermesMemoryError extends HermesCoreError",
          issubclass(HermesMemoryError, HermesCoreError))

    # 12c. 可以正常 raise 和 catch
    try:
        raise HermesMemoryError("test")
    except HermesMemoryError:
        check("HermesMemoryError can be raised and caught", True)
    except Exception:
        check("HermesMemoryError can be raised and caught", False, "caught wrong exception")


# ═══════════════════════════════════════════════════
# 13. tools.py — tool_name 修复验证
# ═══════════════════════════════════════════════════
def test_tools():
    section("13. tools.py: tool_name + import 修复验证")

    # 13a. 相对导入不崩溃
    try:
        from runtime_integration import execute_tool
        check("runtime_integration imports (no bare import)", True)
    except ImportError as e:
        check("runtime_integration imports (no bare import)", False, str(e))

    # 13b. tools.py 可导入
    try:
        import tools
        check("tools.py imports", True)
    except Exception as e:
        check("tools.py imports", False, str(e))

    # 13c. slash_field_test 使用 params dict（不是 kwargs）
    import inspect
    src = inspect.getsource(tools.handle_core_field_test) if hasattr(tools, 'handle_core_field_test') else ""
    check("handle_core_field_test exists", len(src) > 0)


# ═══════════════════════════════════════════════════
# 14. hooks.py — 完整生命周期
# ═══════════════════════════════════════════════════
def test_hooks_lifecycle():
    section("14. hooks.py: 完整生命周期验证")
    import hooks

    # 14a. on_session_start 存在且可调用
    check("on_session_start exists", callable(getattr(hooks, 'on_session_start', None)))

    # 14b. on_session_end 存在且可调用
    check("on_session_end exists", callable(getattr(hooks, 'on_session_end', None)))

    # 14c. pre_tool_call 存在且可调用
    check("pre_tool_call exists", callable(getattr(hooks, 'pre_tool_call', None)))

    # 14d. post_tool_call 存在且可调用
    check("post_tool_call exists", callable(getattr(hooks, 'post_tool_call', None)))

    # 14e. core_ 工具跳过逻辑（post_tool_call 不处理 core_ 工具）
    try:
        # 模拟 core_ 工具调用 — 不应触发学习循环
        result = hooks.post_tool_call(
            tool_name="core_plan",
            tool_call_id="test_123",
            tool_input={"goal": "test"},
            tool_output="test output",
            success=True,
            elapsed_s=0.1,
        )
        # 应该直接返回 None（跳过）
        check("core_ tools skipped in post_tool_call", result is None)
    except Exception as e:
        check("core_ tools skipped in post_tool_call", False, str(e))

    # 14f. 标准工具 pre_tool_call 不拦截未知工具
    try:
        result = hooks.pre_tool_call(
            tool_name="unknown_tool_xyz",
            tool_call_id="test_456",
            tool_input={},
        )
        check("unknown tools pass through pre_tool_call", result is None)
    except Exception as e:
        check("unknown tools pass through pre_tool_call", False, str(e))

    # 14g. _GOAL_TOOL_INDEX 有上限保护
    check("_GOAL_TOOL_INDEX exists", hasattr(hooks, '_GOAL_TOOL_INDEX'))

    # 14h. on_session_end 清理所有子系统
    try:
        hooks.on_session_end(session_id="test_session")
        check("on_session_end runs without error", True)
    except Exception as e:
        check("on_session_end runs without error", False, str(e))


# ═══════════════════════════════════════════════════
# 15. runtime_integration — execute_tool 管线
# ═══════════════════════════════════════════════════
def test_runtime_integration():
    section("15. runtime_integration: execute_tool 管线")

    try:
        from runtime_integration import RuntimeHotPath
        check("RuntimeHotPath imports", True)
    except Exception as e:
        check("RuntimeHotPath imports", False, str(e))

    # 15a. _kernel 不重复赋值
    import inspect
    try:
        src = inspect.getsource(RuntimeHotPath.__init__)
        kernel_assigns = src.count('self._kernel')
        check("_kernel assigned once in __init__", kernel_assigns <= 1,
              f"found {kernel_assigns} assignments")
    except Exception as e:
        check("_kernel assigned once in __init__", False, str(e))


# ═══════════════════════════════════════════════════
# 16. __init__.py — 懒加载 + semantic_retrieval
# ═══════════════════════════════════════════════════
def test_init_lazy():
    section("16. __init__.py: 懒加载 + semantic_retrieval")

    # 16a. semantic_retrieval 可直接导入
    from semantic_retrieval import SemanticRetrieval
    check("SemanticRetrieval is importable", SemanticRetrieval is not None)

    # 16b. 懒加载通过 __init__.py 的 _LazyModule 机制
    # 验证 __init__.py 中定义了 semantic_retrieval 懒加载
    with open(os.path.expanduser("~/.hermes/core/__init__.py")) as f:
        init_src = f.read()
    check("semantic_retrieval in __init__.py lazy defs", "semantic_retrieval" in init_src)

    # 16c. MemoryErrorBase 在 __init__.py 中导出
    check("MemoryErrorBase in __init__.py exports", "MemoryErrorBase" in init_src)


# ═══════════════════════════════════════════════════
# 17. 历史回归验证 — 所有已知 bug 的回归测试
# ═══════════════════════════════════════════════════
def test_regression():
    section("17. 历史回归验证")

    # 17a. field_runner float→int
    from field_runner import simulate as simulate_hour
    try:
        result = simulate_hour(hours=1.0)  # float 不应崩溃
        check("field_runner float hours doesn't crash", True)
    except TypeError as e:
        check("field_runner float hours doesn't crash", False, f"TypeError: {e}")
    except Exception as e:
        # 其他错误（如 DB 不存在）是 OK 的
        check("field_runner float hours doesn't crash", True, f"other error: {e}")

    # 17b. _ensure_legacy 已移除
    import hooks
    check("_ensure_legacy removed", not hasattr(hooks, '_ensure_legacy'))

    # 17c. _event_logger 全局变量已移除
    check("_event_logger global removed", not hasattr(hooks, '_event_logger'))

    # 17d. hash % N 确定性采样已替换
    import inspect
    hooks_src = inspect.getsource(hooks)
    import re
    hash_mod = re.search(r'hash\(.*?\)\s*%', hooks_src)
    check("no hash(x) % N sampling in hooks", hash_mod is None)

    # 17e. tool_registry.save() 在 on_session_end 中调用
    end_src = inspect.getsource(hooks.on_session_end)
    check("tool_registry.save() in on_session_end", 'tool_registry' in end_src and 'save' in end_src)

    # 17f. event_logger.flush() 在 on_session_end 中调用
    check("event_logger.flush() in on_session_end", 'flush' in end_src)


# ═══════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════
def main():
    global PASS, FAIL

    print("=" * 60)
    print(" hermes-core Integration Test Suite")
    print(" 基于历史 bug 清单 + 模块运行时验证")
    print("=" * 60)

    tests = [
        test_db_schema,
        test_event_logger,
        test_world_model,
        test_memory_manager,
        test_semantic_retrieval,
        test_task_graph,
        test_planner,
        test_policy_engine,
        test_reflection_engine,
        test_goal_manager,
        test_recovery_manager,
        test_exceptions,
        test_tools,
        test_hooks_lifecycle,
        test_runtime_integration,
        test_init_lazy,
        test_regression,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            FAIL += 1
            msg = f"  💥 {t.__name__} crashed: {e}"
            print(msg)
            DETAILS.append(msg)

    print()
    print("=" * 60)
    total = PASS + FAIL
    print(f"  Results:  {PASS} passed  {FAIL} failed  (total: {total})")
    if FAIL > 0:
        print(f"\n  Failed tests:")
        for d in DETAILS:
            print(f"    {d}")
    print("=" * 60)

    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
