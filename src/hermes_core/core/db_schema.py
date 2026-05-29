"""
SQLite Schema Management for Hermes Core Databases.

Manages multiple SQLite databases under ~/.hermes/core/data/:
  - world_state.db   (World Model)
  - experience.db    (Experience System)
  - reflection.db    (Reflection Engine)

All databases use WAL mode for better concurrent read performance.
Standard library only: sqlite3, pathlib.
"""

import sqlite3
import threading
import time
from pathlib import Path

DATA_DIR = Path.home() / ".hermes" / "core" / "data"

# ---------------------------------------------------------------------------
# Schema definitions: table name -> CREATE TABLE SQL
# ---------------------------------------------------------------------------

WORLD_STATE_SCHEMA = {
    "system_state": """
        CREATE TABLE IF NOT EXISTS system_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at     TEXT NOT NULL,
            cpu_pct         REAL,
            ram_total_mb    REAL,
            ram_used_mb     REAL,
            ram_avail_mb    REAL,
            disk_total_gb   REAL,
            disk_used_gb    REAL,
            load_1m         REAL,
            network_status  TEXT,
            browser_count   INTEGER DEFAULT 0,
            active_task_id  TEXT
        )
    """,
    "task_history": """
        CREATE TABLE IF NOT EXISTS task_history (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            task_type           TEXT,
            description         TEXT,
            status              TEXT,
            started_at          TEXT,
            completed_at        TEXT,
            duration_seconds    REAL,
            result_summary      TEXT,
            error_message       TEXT,
            resource_peak_ram_mb REAL
        )
    """,
    "website_risk": """
        CREATE TABLE IF NOT EXISTS website_risk (
            domain          TEXT PRIMARY KEY,
            risk_level      INTEGER DEFAULT 0,
            last_tested_at  TEXT,
            success_count   INTEGER DEFAULT 0,
            fail_count      INTEGER DEFAULT 0,
            notes           TEXT
        )
    """,
    "cache_stats": """
        CREATE TABLE IF NOT EXISTS cache_stats (
            cache_key       TEXT PRIMARY KEY,
            hit_count       INTEGER DEFAULT 0,
            miss_count      INTEGER DEFAULT 0,
            last_access_at  TEXT,
            size_bytes      INTEGER
        )
    """,
    "events": """
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id        TEXT UNIQUE,
            event_type      TEXT NOT NULL,
            severity        TEXT NOT NULL DEFAULT 'info',
            session_id      TEXT,
            data_json       TEXT,
            created_at      TEXT NOT NULL
        )
    """,
}

EXPERIENCE_SCHEMA = {
    "successful_patterns": """
        CREATE TABLE IF NOT EXISTS successful_patterns (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_name        TEXT,
            action_sequence     TEXT,
            success_count       INTEGER,
            fail_count          INTEGER,
            avg_duration_seconds REAL,
            last_used_at        TEXT,
            domain              TEXT,
            tags                TEXT,
            confidence          REAL DEFAULT 0.5,
            sample_size         INTEGER DEFAULT 1,
            verification_count  INTEGER DEFAULT 1,
            decay_rate          REAL DEFAULT 0.01,
            last_verified_at    TEXT,
            last_success_at     TEXT
        )
    """,
    "failure_patterns": """
        CREATE TABLE IF NOT EXISTS failure_patterns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            domain          TEXT,
            error_type      TEXT,
            error_message   TEXT,
            count           INTEGER DEFAULT 1,
            first_seen_at   TEXT,
            last_seen_at    TEXT,
            resolution      TEXT
        )
    """,
    "tool_usage_stats": """
        CREATE TABLE IF NOT EXISTS tool_usage_stats (
            tool_name           TEXT PRIMARY KEY,
            call_count          INTEGER,
            success_count       INTEGER,
            fail_count          INTEGER,
            avg_cost            REAL,
            avg_duration_seconds REAL,
            last_used_at        TEXT
        )
    """,
}

REFLECTION_SCHEMA = {
    "reflections": """
        CREATE TABLE IF NOT EXISTS reflections (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            task_id             TEXT,
            task_description    TEXT,
            goal                TEXT,
            result_summary      TEXT,
            success             BOOLEAN,
            mistakes            TEXT,
            improvements        TEXT,
            successful_patterns TEXT,
            created_at          TEXT
        )
    """,
}

MEMORY_SCHEMA = {
    "episodic_memories": """
        CREATE TABLE IF NOT EXISTS episodic_memories (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            episode_key         TEXT,
            task_id             TEXT,
            tool_name           TEXT,
            action_description  TEXT,
            context_before      TEXT,
            context_after       TEXT,
            outcome             TEXT,
            importance          REAL DEFAULT 0.5,
            timestamp           TEXT
        )
    """,
    "semantic_facts": """
        CREATE TABLE IF NOT EXISTS semantic_facts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_key        TEXT UNIQUE,
            fact_value      TEXT,
            domain          TEXT,
            confidence      REAL DEFAULT 0.5,
            source          TEXT,
            created_at      TEXT,
            updated_at      TEXT
        )
    """,
    "procedural_memories": """
        CREATE TABLE IF NOT EXISTS procedural_memories (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            procedure_name  TEXT,
            steps           TEXT,
            domain          TEXT,
            success_rate    REAL DEFAULT 0.0,
            usage_count     INTEGER DEFAULT 0,
            created_at      TEXT,
            updated_at      TEXT
        )
    """,
    "environment_facts": """
        CREATE TABLE IF NOT EXISTS environment_facts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fact_key        TEXT UNIQUE,
            fact_value      TEXT,
            category        TEXT,
            confidence      REAL DEFAULT 0.5,
            discovered_at   TEXT,
            last_verified   TEXT
        )
    """,
}

# Map database name (without extension) to its schema dict
DATABASE_SCHEMAS: dict[str, dict[str, str]] = {
    "world_state": WORLD_STATE_SCHEMA,
    "experience": EXPERIENCE_SCHEMA,
    "memory": MEMORY_SCHEMA,
    "reflection": REFLECTION_SCHEMA,
}

# Map logical DB names → physical file names (merging related DBs)
#   cognitive.db    ← experience, memory, reflection (learning data)
#   performance.db  ← world_state (operational/system data)
_DB_FILE_MAP: dict[str, str] = {
    "experience": "cognitive",
    "memory": "cognitive",
    "reflection": "cognitive",
    "world_state": "performance",
}


# ---------------------------------------------------------------------------
# SchemaManager
# ---------------------------------------------------------------------------

class SchemaManager:
    """Manages SQLite database connections and schema creation/querying.

    Usage:
        mgr = SchemaManager()
        mgr.initialize_all()          # create all databases + tables
        conn = mgr.get_connection("world_state")
        # ... use conn ...
        mgr.close_all()
    """

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)
        # Cache of open connections: {db_name: sqlite3.Connection}
        self._connections: dict[str, sqlite3.Connection] = {}
        self._conn_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def get_connection(self, db_name: str) -> sqlite3.Connection:
        """Return a cached (or newly opened) connection for *db_name*.

        The database file uses ``_DB_FILE_MAP`` to map ``db_name`` to a
        physical file name, allowing multiple logical databases to share
        a single ``.db`` file.  Falls back to ``{db_name}.db`` if no
        mapping exists.  Connections are opened once and reused; WAL
        mode is enabled lazily.
        """
        conn = self._connections.get(db_name)
        if conn is not None:
            return conn

        with self._conn_lock:
            # Double-check after acquiring lock
            conn = self._connections.get(db_name)
            if conn is not None:
                return conn
            file_name = _DB_FILE_MAP.get(db_name, db_name)
            db_path = self._data_dir / f"{file_name}.db"
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.row_factory = sqlite3.Row
            self._connections[db_name] = conn
            return conn

    def close_all(self) -> None:
        """Close and remove all cached database connections."""
        for name, conn in self._connections.items():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._connections.clear()

    def close(self, db_name: str) -> None:
        """Close a single database connection."""
        conn = self._connections.pop(db_name, None)
        if conn:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------
    # Schema initialization
    # ------------------------------------------------------------------

    def initialize_all(self) -> None:
        """Create all known databases and their tables (idempotent)."""
        for db_name in DATABASE_SCHEMAS:
            self.initialize(db_name)

    def initialize(self, db_name: str) -> None:
        """Create tables for *db_name* if they do not already exist.

        Raises ``ValueError`` if *db_name* is not a recognised database.
        """
        schema = DATABASE_SCHEMAS.get(db_name)
        if schema is None:
            raise ValueError(
                f"Unknown database '{db_name}'. "
                f"Known databases: {list(DATABASE_SCHEMAS)}"
            )
        conn = self.get_connection(db_name)
        cursor = conn.cursor()
        for table_name, create_sql in schema.items():
            cursor.execute(create_sql)
        conn.commit()

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_table_info(self, db_name: str, table_name: str) -> list:
        """Return column metadata for *table_name* via ``PRAGMA table_info``.

        Each element is a dict-like ``sqlite3.Row`` with keys:
            cid, name, type, notnull, dflt_value, pk
        Returns an empty list if the table does not exist.
        """
        conn = self.get_connection(db_name)
        # PRAGMA does not support parameterised placeholders — validate name
        self._validate_table_name(table_name)
        cursor = conn.execute(f"PRAGMA table_info(\"{table_name}\");")
        return cursor.fetchall()

    def list_tables(self, db_name: str) -> list[str]:
        """Return a list of user table names in the given database."""
        conn = self.get_connection(db_name)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name;"
        )
        return [row["name"] for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def vacuum(self, db_name: str) -> None:
        """Reclaim storage space with ``VACUUM`` on *db_name*."""
        conn = self.get_connection(db_name)
        conn.execute("VACUUM;")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_table_name(table_name: str) -> None:
        """Validate *table_name* to prevent SQL injection in PRAGMA calls.

        Raises ``ValueError`` if the name is empty or contains characters
        outside ``[a-zA-Z0-9_]``.
        """
        if not table_name or not table_name.strip():
            raise ValueError("Table name must not be empty.")
        if not all(c.isalnum() or c == "_" for c in table_name):
            raise ValueError(
                f"Invalid table name '{table_name}': "
                "only alphanumeric characters and underscores are allowed."
            )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        """Path to the directory holding the SQLite database files."""
        return self._data_dir

    def db_path(self, db_name: str) -> Path:
        """Return the filesystem path for *db_name*.

        Respects ``_DB_FILE_MAP`` — the returned path reflects the
        actual physical file, not the logical DB name.
        """
        file_name = _DB_FILE_MAP.get(db_name, db_name)
        return self._data_dir / f"{file_name}.db"

    def database_exists(self, db_name: str) -> bool:
        """Check whether the database file already exists on disk."""
        return self.db_path(db_name).exists()

    def table_exists(self, db_name: str, table_name: str) -> bool:
        """Check whether *table_name* exists in *db_name*."""
        return table_name in self.list_tables(db_name)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_manager: SchemaManager | None = None


def get_manager() -> SchemaManager:
    """Return the module-level singleton ``SchemaManager``."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SchemaManager()
    return _default_manager


def init() -> None:
    """One-shot: create the data directory and initialise all databases."""
    mgr = get_manager()
    mgr.initialize_all()
