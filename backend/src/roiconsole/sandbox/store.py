"""Shared SQLite store holding sandbox Slack, Notion, and CRM state across processes."""

# ============= Standard Library =============
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ============= Constants =============
BUSY_TIMEOUT_S = 10.0
SANDBOX_TABLES_DDL = [
    "CREATE TABLE IF NOT EXISTS slack_channels (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL)",
    """CREATE TABLE IF NOT EXISTS slack_messages (
        channel_id TEXT NOT NULL, ts TEXT NOT NULL, thread_ts TEXT, user TEXT NOT NULL,
        text TEXT NOT NULL, o_bot INTEGER NOT NULL, run_id TEXT, created_at REAL NOT NULL,
        PRIMARY KEY (channel_id, ts))""",
    """CREATE TABLE IF NOT EXISTS notion_databases (
        id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, title_property TEXT NOT NULL,
        schema TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS notion_pages (
        id TEXT PRIMARY KEY, database_id TEXT NOT NULL, title TEXT NOT NULL,
        properties TEXT NOT NULL, o_archived INTEGER NOT NULL DEFAULT 0, run_id TEXT,
        created_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS notion_blocks (
        id TEXT PRIMARY KEY, page_id TEXT NOT NULL, position INTEGER NOT NULL,
        type TEXT NOT NULL, text TEXT NOT NULL, checked INTEGER, created_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS crm_companies (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT)""",
    """CREATE TABLE IF NOT EXISTS crm_people (
        id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL, email TEXT,
        company_id TEXT, job_title TEXT, fields TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS crm_opportunities (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, stage TEXT NOT NULL, amount REAL NOT NULL,
        currency TEXT NOT NULL, company_id TEXT, point_of_contact_id TEXT, close_date TEXT)""",
    """CREATE TABLE IF NOT EXISTS crm_activities (
        id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, note TEXT NOT NULL, run_id TEXT,
        created_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS idempotency (
        key TEXT PRIMARY KEY, server TEXT NOT NULL, tool TEXT NOT NULL, result TEXT NOT NULL,
        created_at REAL NOT NULL)""",
]
RESETTABLE_TABLES = (
    "slack_channels",
    "slack_messages",
    "notion_databases",
    "notion_pages",
    "notion_blocks",
    "crm_companies",
    "crm_people",
    "crm_opportunities",
    "crm_activities",
    "idempotency",
)
SANDBOX_BOT_USER = "roi-console-bot"


# ============= Store =============


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601."""
    return datetime.now(UTC).isoformat()


class SandboxStore:
    """
    File-backed sandbox state shared by MCP server processes and validators.

    Parameters
    ----------
    path : Path
        SQLite file. Separate processes open the same file; WAL mode keeps
        concurrent readers and a writer safe.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ts_lock = threading.Lock()
        self._last_ts = 0.0
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            for ddl in SANDBOX_TABLES_DDL:
                conn.execute(ddl)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a committing connection to the sandbox file."""
        conn = sqlite3.connect(str(self.path), timeout=BUSY_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def reset(self) -> None:
        """Delete all sandbox state."""
        with self.connect() as conn:
            for table in RESETTABLE_TABLES:
                conn.execute(f"DELETE FROM {table}")

    def next_ts(self) -> str:
        """Return a strictly increasing Slack-style timestamp string."""
        with self._ts_lock:
            candidate = round(time.time(), 6)
            if candidate <= self._last_ts:
                candidate = round(self._last_ts + 0.000001, 6)
            self._last_ts = candidate
        return f"{candidate:.6f}"

    # ============= Slack =============

    def ensure_channel(self, name: str) -> str:
        """Create a channel if missing and return its id."""
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM slack_channels WHERE name = ?", (name,)).fetchone()
            if row:
                return row["id"]
            channel_id = f"C{uuid.uuid4().hex[:10].upper()}"
            conn.execute("INSERT INTO slack_channels VALUES (?, ?)", (channel_id, name))
            return channel_id

    def channel_id(self, name_or_id: str) -> str | None:
        """Resolve a channel name or id to an id."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM slack_channels WHERE name = ? OR id = ?", (name_or_id, name_or_id)
            ).fetchone()
        return row["id"] if row else None

    def channel_name(self, channel_id: str) -> str | None:
        """Return the channel name for an id."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT name FROM slack_channels WHERE id = ?", (channel_id,)
            ).fetchone()
        return row["name"] if row else None

    def list_channels(self) -> list[dict[str, str]]:
        """Return all channels as {id, name}."""
        with self.connect() as conn:
            rows = conn.execute("SELECT id, name FROM slack_channels ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def add_message(
        self,
        channel_id: str,
        text: str,
        user: str = SANDBOX_BOT_USER,
        thread_ts: str | None = None,
        o_bot: bool = True,
        run_id: str | None = None,
    ) -> str:
        """Insert a message and return its ts."""
        ts = self.next_ts()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO slack_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (channel_id, ts, thread_ts, user, text, int(o_bot), run_id, time.time()),
            )
        return ts

    def thread_messages(self, channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        """Return the parent message and replies of a thread, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM slack_messages WHERE channel_id = ? AND (ts = ? OR thread_ts = ?) "
                "ORDER BY ts",
                (channel_id, thread_ts, thread_ts),
            ).fetchall()
        return [dict(r) for r in rows]

    def channel_messages(self, channel_id: str, since: float = 0.0) -> list[dict[str, Any]]:
        """Return every message in a channel created at or after `since` (epoch seconds)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM slack_messages WHERE channel_id = ? AND created_at >= ? ORDER BY ts",
                (channel_id, since),
            ).fetchall()
        return [dict(r) for r in rows]

    # ============= Notion =============

    def ensure_database(self, name: str, title_property: str, schema_dict: dict[str, str]) -> str:
        """Create a database if missing and return its id."""
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM notion_databases WHERE name = ?", (name,)).fetchone()
            if row:
                return row["id"]
            database_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO notion_databases VALUES (?, ?, ?, ?)",
                (database_id, name, title_property, json.dumps(schema_dict)),
            )
            return database_id

    def database(self, name_or_id: str) -> dict[str, Any] | None:
        """Return a database row by name or id, with its schema decoded."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM notion_databases WHERE name = ? OR id = ?", (name_or_id, name_or_id)
            ).fetchone()
        if not row:
            return None
        database_dict = dict(row)
        database_dict["schema"] = json.loads(database_dict["schema"])
        return database_dict

    def add_page(
        self,
        database_id: str,
        title: str,
        properties_dict: dict[str, Any],
        run_id: str | None = None,
    ) -> str:
        """Insert a page and return its id."""
        page_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO notion_pages VALUES (?, ?, ?, ?, 0, ?, ?)",
                (page_id, database_id, title, json.dumps(properties_dict), run_id, time.time()),
            )
        return page_id

    def page(self, page_id: str) -> dict[str, Any] | None:
        """Return a page row with properties decoded."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM notion_pages WHERE id = ?", (page_id,)).fetchone()
        if not row:
            return None
        page_dict = dict(row)
        page_dict["properties"] = json.loads(page_dict["properties"])
        return page_dict

    def database_pages(self, database_id: str, since: float = 0.0) -> list[dict[str, Any]]:
        """Return non-archived pages of a database created at or after `since`."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notion_pages WHERE database_id = ? AND o_archived = 0 "
                "AND created_at >= ? ORDER BY created_at",
                (database_id, since),
            ).fetchall()
        page_list = [dict(r) for r in rows]
        for page_dict in page_list:
            page_dict["properties"] = json.loads(page_dict["properties"])
        return page_list

    def append_blocks(self, page_id: str, block_list: list[dict[str, Any]]) -> list[str]:
        """Append simplified blocks to a page and return their ids."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS pos FROM notion_blocks WHERE page_id = ?",
                (page_id,),
            ).fetchone()
            position = int(row["pos"]) + 1
            id_list = []
            for block in block_list:
                block_id = str(uuid.uuid4())
                checked = block.get("checked")
                conn.execute(
                    "INSERT INTO notion_blocks VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        block_id,
                        page_id,
                        position,
                        block["type"],
                        block.get("text", ""),
                        None if checked is None else int(bool(checked)),
                        time.time(),
                    ),
                )
                id_list.append(block_id)
                position += 1
        return id_list

    def page_blocks(self, page_id: str) -> list[dict[str, Any]]:
        """Return a page's blocks in order."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notion_blocks WHERE page_id = ? ORDER BY position", (page_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ============= CRM =============

    def add_company(
        self, name: str, domain: str | None = None, company_id: str | None = None
    ) -> str:
        """Insert a company and return its id."""
        company_id = company_id or str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute("INSERT INTO crm_companies VALUES (?, ?, ?)", (company_id, name, domain))
        return company_id

    def add_person(self, person_dict: dict[str, Any], person_id: str | None = None) -> str:
        """Insert a person and return their id."""
        person_id = person_id or str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO crm_people VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    person_id,
                    person_dict["first_name"],
                    person_dict["last_name"],
                    person_dict.get("email"),
                    person_dict.get("company_id"),
                    person_dict.get("job_title"),
                    json.dumps(person_dict.get("fields", {})),
                    person_dict.get("created_at") or _now_iso(),
                ),
            )
        return person_id

    def person(self, person_id: str) -> dict[str, Any] | None:
        """Return a person joined with their company name."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT p.*, c.name AS company FROM crm_people p "
                "LEFT JOIN crm_companies c ON c.id = p.company_id WHERE p.id = ?",
                (person_id,),
            ).fetchone()
        if not row:
            return None
        person_dict = dict(row)
        person_dict["fields"] = json.loads(person_dict["fields"])
        return person_dict

    def people_since(self, since_iso: str) -> list[dict[str, Any]]:
        """Return people created at or after an ISO timestamp."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT p.*, c.name AS company FROM crm_people p "
                "LEFT JOIN crm_companies c ON c.id = p.company_id "
                "WHERE p.created_at >= ? ORDER BY p.created_at",
                (since_iso,),
            ).fetchall()
        people_list = [dict(r) for r in rows]
        for person_dict in people_list:
            person_dict["fields"] = json.loads(person_dict["fields"])
        return people_list

    def update_person(self, person_id: str, fields_dict: dict[str, Any]) -> dict[str, Any] | None:
        """Merge fields into a person's custom fields and return the updated record."""
        current = self.person(person_id)
        if current is None:
            return None
        merged_dict = {**current["fields"], **fields_dict}
        with self.connect() as conn:
            conn.execute(
                "UPDATE crm_people SET fields = ? WHERE id = ?",
                (json.dumps(merged_dict), person_id),
            )
        return self.person(person_id)

    def add_opportunity(self, opportunity_dict: dict[str, Any], opp_id: str | None = None) -> str:
        """Insert an opportunity and return its id."""
        opp_id = opp_id or str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO crm_opportunities VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    opp_id,
                    opportunity_dict["name"],
                    opportunity_dict["stage"],
                    float(opportunity_dict["amount"]),
                    opportunity_dict.get("currency", "USD"),
                    opportunity_dict.get("company_id"),
                    opportunity_dict.get("point_of_contact_id"),
                    opportunity_dict.get("close_date"),
                ),
            )
        return opp_id

    def opportunities(self, stage_list: list[str]) -> list[dict[str, Any]]:
        """Return opportunities in the given stages, with company and contact names."""
        placeholders = ", ".join("?" for _ in stage_list)
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT o.*, c.name AS company, p.first_name, p.last_name FROM crm_opportunities o "
                "LEFT JOIN crm_companies c ON c.id = o.company_id "
                "LEFT JOIN crm_people p ON p.id = o.point_of_contact_id "
                f"WHERE o.stage IN ({placeholders}) ORDER BY o.name",
                stage_list,
            ).fetchall()
        return [dict(r) for r in rows]

    def add_activity(self, contact_id: str, note: str, run_id: str | None = None) -> str:
        """Insert a CRM activity note and return its id."""
        activity_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO crm_activities VALUES (?, ?, ?, ?, ?)",
                (activity_id, contact_id, note, run_id, time.time()),
            )
        return activity_id

    def activities(self, since: float = 0.0) -> list[dict[str, Any]]:
        """Return activities created at or after `since`."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crm_activities WHERE created_at >= ? ORDER BY created_at", (since,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ============= Idempotency =============

    def idempotent_result(self, key: str) -> dict[str, Any] | None:
        """Return the stored result for an idempotency key, if any."""
        with self.connect() as conn:
            row = conn.execute("SELECT result FROM idempotency WHERE key = ?", (key,)).fetchone()
        return json.loads(row["result"]) if row else None

    def store_idempotent_result(
        self, key: str, server: str, tool: str, result_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Store a result under a key unless one exists, and return the winning result.

        Uses INSERT OR IGNORE so two concurrent writers with the same key agree on
        the first stored result.
        """
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency VALUES (?, ?, ?, ?, ?)",
                (key, server, tool, json.dumps(result_dict), time.time()),
            )
            row = conn.execute("SELECT result FROM idempotency WHERE key = ?", (key,)).fetchone()
        return json.loads(row["result"])
