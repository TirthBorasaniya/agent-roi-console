"""Shared MCP server plumbing: call context, call logging, idempotency, allowlists, throttling."""

# ============= Standard Library =============
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ============= Third-Party =============
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError

# ============= Local =============
from roiconsole.common.ids import idempotency_key as compute_idempotency_key

# ============= Constants =============
logger = logging.getLogger(__name__)

PENDING_MARKER = {"__pending__": True}
CLAIM_WAIT_S = 30.0
CLAIM_POLL_S = 0.1
IDEMPOTENCY_DDL = """
CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY, server TEXT NOT NULL, tool TEXT NOT NULL,
    result TEXT NOT NULL, created_at REAL NOT NULL)
"""


# ============= Call Context =============


@dataclass(frozen=True)
class CallContext:
    """Run and step ids the client attached to a tool call through request `_meta`."""

    run_id: str
    step_id: str


def call_context(ctx: Context | None) -> CallContext:
    """
    Extract run_id and step_id from an MCP request's `_meta`.

    Parameters
    ----------
    ctx : Context or None
        FastMCP request context injected into the tool.

    Returns
    -------
    context : CallContext
        Ids, or "unknown" when the client sent none.
    """
    meta = None
    if ctx is not None:
        try:
            meta = ctx.request_context.meta
        except (ValueError, LookupError):
            meta = None
    extra_dict = (getattr(meta, "model_extra", None) or {}) if meta is not None else {}
    return CallContext(
        run_id=str(extra_dict.get("run_id", "unknown")),
        step_id=str(extra_dict.get("step_id", "unknown")),
    )


def log_call(
    server: str, tool: str, context: CallContext, status: str, latency_s: float, **extra: Any
) -> None:
    """Log one tool call as a structured record with run_id and step_id."""
    logger.info(
        "tool_call",
        extra={
            "server": server,
            "tool": tool,
            "run_id": context.run_id,
            "step_id": context.step_id,
            "status": status,
            "latency_s": round(latency_s, 4),
            **extra,
        },
    )


# ============= Idempotency =============


class IdempotencyStore:
    """
    First-result-wins store for write tools, safe across threads and processes.

    A write claims its key before touching the backend. A concurrent duplicate
    waits for the first writer's result instead of writing a second time; a
    failed write releases its claim so a retry can proceed.

    Parameters
    ----------
    path : Path
        SQLite file for the idempotency table.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(IDEMPOTENCY_DDL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a committing connection."""
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def claim(self, key: str, server: str, tool: str) -> tuple[bool, dict[str, Any] | None]:
        """
        Claim a key or return the stored result of the first writer.

        Returns
        -------
        o_claimed : bool
            True when this caller must perform the write.
        result : dict or None
            The first writer's result when not claimed.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO idempotency VALUES (?, ?, ?, ?, ?)",
                (key, server, tool, json.dumps(PENDING_MARKER), time.time()),
            )
            if cursor.rowcount == 1:
                return True, None
        deadline = time.monotonic() + CLAIM_WAIT_S
        while time.monotonic() < deadline:
            stored = self.result(key)
            if stored is None:
                # the first writer failed and released its claim; take it over
                return self.claim(key, server, tool)
            if stored != PENDING_MARKER:
                return False, stored
            time.sleep(CLAIM_POLL_S)
        raise ToolError(f"idempotency key {key} is still pending after {CLAIM_WAIT_S}s")

    def result(self, key: str) -> dict[str, Any] | None:
        """Return the stored result or pending marker for a key, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT result FROM idempotency WHERE key = ?", (key,)).fetchone()
        return json.loads(row["result"]) if row else None

    def complete(self, key: str, result_dict: dict[str, Any]) -> None:
        """Store the result for a claimed key."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE idempotency SET result = ? WHERE key = ?", (json.dumps(result_dict), key)
            )

    def release(self, key: str) -> None:
        """Drop a claim after a failed write."""
        with self._connect() as conn:
            conn.execute("DELETE FROM idempotency WHERE key = ?", (key,))

    def clear(self) -> None:
        """Delete every key, used when a sandbox workspace is reset."""
        with self._connect() as conn:
            conn.execute("DELETE FROM idempotency")


# ============= Throttling =============


class WriteThrottle:
    """
    Enforce a minimum interval between writes to stay inside provider rate limits.

    Parameters
    ----------
    min_interval_s : float
        Minimum seconds between consecutive writes; 0 disables throttling.
    """

    def __init__(self, min_interval_s: float) -> None:
        self._min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        """Block until the next write is allowed."""
        if self._min_interval_s <= 0:
            return
        with self._lock:
            delay = self._last + self._min_interval_s - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


# ============= Write Wrapper =============


def check_allowlisted(kind: str, name: str, allowlist: list[str] | set[str]) -> None:
    """Raise ToolError when a channel or database is outside the configured allowlist."""
    if name not in allowlist:
        raise ToolError(f"{kind} {name!r} is not in the allowlist")


def idempotent_write(
    store: IdempotencyStore,
    server: str,
    tool: str,
    context: CallContext,
    arguments: dict[str, Any],
    idempotency_key: str,
    write: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """
    Perform a write at most once per idempotency key.

    Parameters
    ----------
    store : IdempotencyStore
        Shared key store.
    server, tool : str
        Names recorded with the key.
    context : CallContext
        Run and step ids, used for logging and to check the key.
    arguments : dict
        Tool arguments without the key.
    idempotency_key : str
        Client-supplied key. An empty key is rejected, because a write without
        one could be applied twice on retry.
    write : callable
        Performs the backend write and returns the result dict.

    Returns
    -------
    result : dict
        The first result stored under the key, with `idempotent_replay` set to
        True when this call did not write.
    """
    if not idempotency_key:
        raise ToolError(f"{tool} requires an idempotency_key")
    started = time.monotonic()
    expected = compute_idempotency_key(context.run_id, context.step_id, arguments)
    if context.run_id != "unknown" and expected != idempotency_key:
        # the key is accepted as given; the mismatch is logged so drift is visible
        logger.warning("idempotency key does not match hash", extra={"tool": tool})
    o_claimed, stored = store.claim(idempotency_key, server, tool)
    if not o_claimed:
        log_call(server, tool, context, "replayed", time.monotonic() - started)
        return {**(stored or {}), "idempotent_replay": True}
    try:
        result_dict = write()
    except Exception:
        store.release(idempotency_key)
        log_call(server, tool, context, "error", time.monotonic() - started)
        raise
    store.complete(idempotency_key, result_dict)
    log_call(server, tool, context, "ok", time.monotonic() - started)
    return {**result_dict, "idempotent_replay": False}


def logged_read(
    server: str, tool: str, context: CallContext, read: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """Run a read tool and log it with run_id and step_id."""
    started = time.monotonic()
    try:
        result_dict = read()
    except Exception:
        log_call(server, tool, context, "error", time.monotonic() - started)
        raise
    log_call(server, tool, context, "ok", time.monotonic() - started)
    return result_dict
