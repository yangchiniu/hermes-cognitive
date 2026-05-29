"""Tests for semantic_retrieval.py and its integration with memory_manager."""
import os
import sys
import tempfile
import sqlite3
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.hermes/core"))

PASS = 0
FAIL = 0

def report(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail}")


def setup_test_db():
    """Create a temp DB with test data using memory_manager's schema."""
    tmpdir = tempfile.mkdtemp()
    data_dir = Path(tmpdir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Register memory_manager's schema (it overrides db_schema's default)
    import db_schema as _db_schema_mod
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

    from db_schema import SchemaManager
    sm = SchemaManager(data_dir)
    sm.initialize("memory")
    conn = sm.get_connection("memory")

    # Insert test episodic memories
    conn.execute("INSERT INTO episodic_memories (session_id, description, summary, outcome, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                 ("s1", "Implemented TF-IDF search module", "TF-IDF module created", "Success"))
    conn.execute("INSERT INTO episodic_memories (session_id, description, summary, outcome, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                 ("s1", "Fixed planner LLM API endpoint configuration", "Planner API fixed", "Success"))
    conn.execute("INSERT INTO episodic_memories (session_id, description, summary, outcome, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
                 ("s2", "Analyzed memory system architecture", "Memory analysis done", "Partial"))

    # Insert test semantic facts
    conn.execute("INSERT INTO semantic_facts (fact, category, confidence, created_at) VALUES (?, ?, ?, datetime('now'))",
                 ("Python uses GIL for thread safety", "programming", 0.95))
    conn.execute("INSERT INTO semantic_facts (fact, category, confidence, created_at) VALUES (?, ?, ?, datetime('now'))",
                 ("SQLite supports WAL mode for concurrent reads", "database", 0.90))

    # Insert test procedures
    conn.execute("""INSERT INTO procedural_memories
        (skill_name, trigger_conditions, steps, domain) VALUES (?, ?, ?, ?)""",
        ("run_tests", "when code changes are made", "cd tests && python3 test_all.py", "devops"))

    conn.commit()
    return data_dir, sm


# ─── Test 1: Tokenizer ───
def test_tokenizer():
    from semantic_retrieval import _tokenize
    tokens = _tokenize("The quick brown fox jumps over the lazy dog")
    report("tokenizer removes stopwords", "the" not in tokens and "over" not in tokens)
    report("tokenizer keeps content words", "quick" in tokens and "jumps" in tokens)
    report("tokenizer lowercases", all(t == t.lower() for t in tokens))
    report("tokenizer handles empty", _tokenize("") == [])


# ─── Test 2: Index and search ───
def test_index_and_search():
    from semantic_retrieval import SemanticRetrieval
    sr = SemanticRetrieval()
    # Reset singleton
    sr.__class__._instance = None

    sr2 = SemanticRetrieval()
    sr2.index_memories([
        {"memory_id": "m1", "text": "Python GIL thread safety", "category": "tech"},
        {"memory_id": "m2", "text": "SQLite WAL mode concurrent reads", "category": "db"},
        {"memory_id": "m3", "text": "Python asyncio event loop", "category": "tech"},
    ])
    report("index total_docs", sr2._total_docs == 3)

    results = sr2.search("python thread", top_k=2)
    report("search returns results", len(results) > 0)
    report("search result has score", "score" in results[0] if results else False)
    report("search result has memory_id", "memory_id" in results[0] if results else False)
    report("search ranks python results first", results[0]["memory_id"] in ("m1", "m3") if results else False)


# ─── Test 3: Category filter ───
def test_category_filter():
    from semantic_retrieval import SemanticRetrieval
    sr = SemanticRetrieval()
    sr.__class__._instance = None
    sr = SemanticRetrieval()
    sr.index_memories([
        {"memory_id": "a1", "text": "deploy server production", "category": "ops"},
        {"memory_id": "b1", "text": "deploy function code", "category": "dev"},
    ])
    results = sr.search_by_category("deploy", category="ops", top_k=5)
    report("category filter returns only matching", all(r["category"] == "ops" for r in results))
    report("category filter finds ops result", any(r["memory_id"] == "a1" for r in results))


# ─── Test 4: find_similar ───
def test_find_similar():
    from semantic_retrieval import SemanticRetrieval
    sr = SemanticRetrieval()
    sr.__class__._instance = None
    sr = SemanticRetrieval()
    sr.index_memories([
        {"memory_id": "x1", "text": "machine learning model training", "category": "ml"},
        {"memory_id": "x2", "text": "web server deployment nginx", "category": "ops"},
    ])
    results = sr.find_similar("deep learning neural network", top_k=2)
    report("find_similar returns results", len(results) > 0)
    report("find_similar ranks ML higher", results[0]["memory_id"] == "x1" if results else False)


# ─── Test 5: Integration with MemoryManager ───
def test_memory_manager_integration():
    data_dir, sm = setup_test_db()

    # Import after setup to use test DB
    from memory_manager import MemoryManager
    mm = MemoryManager(data_dir)

    # Test build_semantic_index
    count = mm.build_semantic_index()
    report("build_semantic_index counts docs", count == 6, f"got {count}")

    # Test semantic_search
    results = mm.semantic_search("TF-IDF search", top_k=3)
    report("semantic_search returns results", len(results) > 0)
    report("semantic_search has scores", all("score" in r for r in results))

    # Test search_all includes semantic_tfidf
    all_results = mm.search_all("planner")
    report("search_all has semantic_tfidf key", "semantic_tfidf" in all_results)
    report("search_all semantic_tfidf is list", isinstance(all_results["semantic_tfidf"], list))

    # Cleanup
    import shutil
    shutil.rmtree(data_dir.parent, ignore_errors=True)


# ─── Run all ───
if __name__ == "__main__":
    print("\n─── semantic_retrieval tests ───")
    test_tokenizer()
    test_index_and_search()
    test_category_filter()
    test_find_similar()
    test_memory_manager_integration()

    print(f"\n  Results:  {PASS} passed  {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)
