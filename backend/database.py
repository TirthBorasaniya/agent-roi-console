"""SQLite database setup, table definitions, and seed data for the Agent ROI Console."""

# ============= Standard Library =============
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ============= Constants =============
DB_DIR = Path("/app/data")
DB_PATH = DB_DIR / "roi_console.db"

SEED_WORKFLOWS = [
    {
        "name": "Slack Channel Digest",
        "description": "Reads recent Slack messages and posts a structured summary",
        "value_category": "SUMMARIZATION",
        "baseline_minutes": 15.0,
    },
    {
        "name": "Notion Knowledge Search",
        "description": "Searches Notion workspace and returns relevant page summaries",
        "value_category": "RESEARCH",
        "baseline_minutes": 10.0,
    },
    {
        "name": "Meeting Notes to Notion",
        "description": "Takes meeting notes as input and creates a structured Notion page",
        "value_category": "DATA_ENTRY",
        "baseline_minutes": 20.0,
    },
    {
        "name": "CRM Pipeline Summary",
        "description": "Lists open opportunities from the CRM and posts a pipeline summary to Slack",
        "value_category": "COORDINATION",
        "baseline_minutes": 12.0,
    },
]

DDL_WORKFLOWS = """
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    value_category TEXT NOT NULL,
    baseline_minutes REAL NOT NULL,
    created_at TEXT NOT NULL
)
"""

DDL_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    input_payload TEXT,
    output_summary TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    token_cost_usd REAL DEFAULT 0.0,
    net_roi_usd REAL DEFAULT 0.0,
    tools_used TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
)
"""

DDL_TOOL_USAGE = """
CREATE TABLE IF NOT EXISTS tool_usage (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    latency_ms INTEGER DEFAULT 0,
    called_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
)
"""


# ============= Connection =============

def get_connection() -> sqlite3.Connection:
    """
    Return a SQLite connection with row_factory set to Row.

    Returns
    -------
    sqlite3.Connection
        Connection to the SQLite database file.
    """
    # allow use from any directory; db path is absolute inside the container
    db_path = DB_PATH if DB_PATH.parent.exists() else Path("data/roi_console.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ============= Initialization =============

def init_db() -> None:
    """
    Create tables and seed example workflows if the workflows table is empty.

    Returns
    -------
    None
    """
    conn = get_connection()
    try:
        conn.execute(DDL_WORKFLOWS)
        conn.execute(DDL_RUNS)
        conn.execute(DDL_TOOL_USAGE)
        conn.commit()
        _seed_workflows(conn)
    finally:
        conn.close()


def _seed_workflows(conn: sqlite3.Connection) -> None:
    """
    Insert the three example workflows if the table is empty.

    Parameters
    ----------
    conn : sqlite3.Connection
        Open database connection.

    Returns
    -------
    None
    """
    row = conn.execute("SELECT COUNT(*) as cnt FROM workflows").fetchone()
    if row["cnt"] > 0:
        return

    now = datetime.now(timezone.utc).isoformat()
    for workflow_dict in SEED_WORKFLOWS:
        conn.execute(
            "INSERT INTO workflows (id, name, description, value_category, baseline_minutes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                workflow_dict["name"],
                workflow_dict["description"],
                workflow_dict["value_category"],
                workflow_dict["baseline_minutes"],
                now,
            ),
        )
    conn.commit()
