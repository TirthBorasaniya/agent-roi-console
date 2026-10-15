"""SQLite persistence for workflows, runs, steps, model calls, tool calls, and guard actions."""

# ============= Standard Library =============
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.settings import get_settings

# ============= Constants =============
BUSY_TIMEOUT_MS = 10_000

SCHEMA_DDL_LIST = [
    """
    CREATE TABLE IF NOT EXISTS workflows (
        workflow_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        value_category TEXT,
        definition_path TEXT NOT NULL,
        definition_sha256 TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_state (
        workflow_id TEXT PRIMARY KEY,
        active_path TEXT NOT NULL,
        status TEXT NOT NULL,
        guard_phase TEXT NOT NULL,
        phase_started_at TEXT,
        phase_run_offset INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        task_id TEXT,
        path TEXT NOT NULL,
        model_config TEXT NOT NULL,
        trigger TEXT NOT NULL,
        status TEXT NOT NULL,
        success INTEGER,
        failure_reason TEXT,
        validator_results TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        llm_cost_usd REAL NOT NULL DEFAULT 0.0,
        tool_cost_usd REAL NOT NULL DEFAULT 0.0,
        latency_s REAL,
        o_demo INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_workflow ON runs (workflow_id, started_at)",
    """
    CREATE TABLE IF NOT EXISTS run_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL,
        latency_s REAL,
        detail TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_steps_run ON run_steps (run_id)",
    """
    CREATE TABLE IF NOT EXISTS llm_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        workflow_id TEXT,
        run_id TEXT,
        step_id TEXT,
        step_class TEXT,
        model TEXT,
        provider TEXT,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0.0,
        cache TEXT NOT NULL DEFAULT 'miss',
        fallback_depth INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL,
        error TEXT,
        latency_s REAL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_run ON llm_calls (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_llm_calls_workflow ON llm_calls (workflow_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS tool_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        server TEXT NOT NULL,
        tool TEXT NOT NULL,
        idempotency_key TEXT,
        status TEXT NOT NULL,
        latency_s REAL,
        error TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls (run_id)",
    """
    CREATE TABLE IF NOT EXISTS guard_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id TEXT NOT NULL,
        action TEXT NOT NULL,
        from_path TEXT,
        to_path TEXT,
        window_runs INTEGER NOT NULL,
        net_roi_usd REAL,
        detail TEXT,
        created_at TEXT NOT NULL
    )
    """,
]


# ============= Connection =============


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Open a SQLite connection with Row factory, WAL journaling, and a busy timeout.

    Parameters
    ----------
    db_path : Path or None
        Database file. Defaults to the settings path.

    Returns
    -------
    conn : sqlite3.Connection
        Open connection. The caller closes it.
    """
    path = db_path or get_settings().resolved_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    # WAL lets the gateway, API, and runner processes write the same file
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success and always closes."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Create every table and index if missing."""
    with session(db_path) as conn:
        for ddl in SCHEMA_DDL_LIST:
            conn.execute(ddl)


# ============= Helpers =============


def insert_row(conn: sqlite3.Connection, table: str, row_dict: dict[str, Any]) -> int:
    """
    Insert one row, serializing dict and list values to JSON.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open connection.
    table : str
        Table name from SCHEMA_DDL_LIST.
    row_dict : dict
        Column name to value.

    Returns
    -------
    rowid : int
        SQLite rowid of the inserted row.
    """
    columns_list = list(row_dict)
    values_list = [json.dumps(v) if isinstance(v, dict | list) else v for v in row_dict.values()]
    placeholders = ", ".join("?" for _ in columns_list)
    cursor = conn.execute(
        f"INSERT INTO {table} ({', '.join(columns_list)}) VALUES ({placeholders})",
        values_list,
    )
    return int(cursor.lastrowid or 0)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert Row objects to plain dicts."""
    return [dict(row) for row in rows]
