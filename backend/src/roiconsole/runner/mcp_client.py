"""MCP client adapter: connect to the three connector servers and call their tools."""

# ============= Standard Library =============
import json
import logging
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ============= Third-Party =============
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

# ============= Local =============
from roiconsole.common.db import insert_row, session, utc_now
from roiconsole.common.ids import idempotency_key
from roiconsole.common.settings import REPO_ROOT, Settings

# ============= Constants =============
logger = logging.getLogger(__name__)

SERVER_NAMES = ("slack", "notion", "crm")
IDEMPOTENCY_PARAM = "idempotency_key"
FUNCTION_SEPARATOR = "__"


# ============= Types =============


class ToolCallError(RuntimeError):
    """Raised when an MCP tool returns an error result."""


@dataclass(frozen=True)
class ToolSpec:
    """One MCP tool as seen by the runner."""

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        """Return `server.tool`, the name used in workflow definitions."""
        return f"{self.server}.{self.name}"

    @property
    def function_name(self) -> str:
        """Return `server__tool`, a name valid for OpenAI function calling."""
        return f"{self.server}{FUNCTION_SEPARATOR}{self.name}"

    @property
    def o_write(self) -> bool:
        """True when the tool takes an idempotency key, i.e. it writes."""
        return IDEMPOTENCY_PARAM in self.input_schema.get("properties", {})

    def openai_schema(self) -> dict[str, Any]:
        """Return an OpenAI tool definition; the runner injects the idempotency key itself."""
        schema = json.loads(json.dumps(self.input_schema))
        schema.get("properties", {}).pop(IDEMPOTENCY_PARAM, None)
        schema["required"] = [r for r in schema.get("required", []) if r != IDEMPOTENCY_PARAM]
        return {
            "type": "function",
            "function": {
                "name": self.function_name,
                "description": self.description,
                "parameters": schema,
            },
        }


@dataclass
class ToolCallRecord:
    """Outcome of one tool call, also written to the tool_calls table."""

    run_id: str
    step_id: str
    server: str
    tool: str
    status: str
    latency_s: float
    idempotency_key: str | None = None
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


# ============= Recorder =============


class ToolCallRecorder:
    """
    Persist tool calls to the console database for per-run traces.

    Parameters
    ----------
    db_path : Path or None
        Console database; None uses the settings path.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    def record(self, call: ToolCallRecord) -> None:
        """Insert one tool_calls row."""
        with session(self._db_path) as conn:
            insert_row(
                conn,
                "tool_calls",
                {
                    "run_id": call.run_id,
                    "step_id": call.step_id,
                    "server": call.server,
                    "tool": call.tool,
                    "idempotency_key": call.idempotency_key,
                    "status": call.status,
                    "latency_s": round(call.latency_s, 4),
                    "error": call.error,
                    "created_at": utc_now(),
                },
            )


# ============= Toolbox =============


def parse_tool_result(result: Any) -> dict[str, Any]:
    """
    Convert a CallToolResult into a dict.

    Prefers structuredContent; falls back to parsing the first text block as JSON.
    """
    if result.structuredContent is not None:
        structured = result.structuredContent
        # FastMCP wraps non-object returns as {"result": ...}
        return structured if isinstance(structured, dict) else {"result": structured}
    text = "".join(getattr(block, "text", "") for block in result.content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


class McpToolbox:
    """
    Calls MCP tools on the connector servers with run and step metadata.

    Parameters
    ----------
    sessions_dict : dict
        Server name (slack, notion, crm) to an initialized ClientSession.
    recorder : ToolCallRecorder or None
        Persists each call; None disables persistence.
    """

    def __init__(
        self, sessions_dict: dict[str, ClientSession], recorder: ToolCallRecorder | None = None
    ) -> None:
        self._sessions_dict = sessions_dict
        self._recorder = recorder
        self._specs_dict: dict[str, ToolSpec] = {}

    @classmethod
    async def connect(
        cls,
        stack: AsyncExitStack,
        settings: Settings,
        recorder: ToolCallRecorder | None = None,
        server_list: tuple[str, ...] = SERVER_NAMES,
    ) -> "McpToolbox":
        """
        Open sessions to the servers: streamable HTTP when a URL is configured, else stdio.

        Parameters
        ----------
        stack : AsyncExitStack
            Owns the transports; closing it closes every session.
        settings : Settings
            Supplies SLACK_MCP_URL, NOTION_MCP_URL, and CRM_MCP_URL.
        recorder : ToolCallRecorder or None
            Persists tool calls.
        server_list : tuple of str
            Servers to connect.

        Returns
        -------
        toolbox : McpToolbox
            Connected toolbox.
        """
        url_dict = {
            "slack": settings.slack_mcp_url,
            "notion": settings.notion_mcp_url,
            "crm": settings.crm_mcp_url,
        }
        sessions_dict: dict[str, ClientSession] = {}
        for server in server_list:
            if url_dict[server]:
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(url_dict[server])
                )
            else:
                env_dict = dict(os.environ)
                env_dict["PYTHONPATH"] = os.pathsep.join(
                    [str(REPO_ROOT / "backend" / "src"), env_dict.get("PYTHONPATH", "")]
                )
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", f"roiconsole.mcp_servers.{server}_server", "--transport", "stdio"],
                    env=env_dict,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            client_session = await stack.enter_async_context(ClientSession(read, write))
            await client_session.initialize()
            sessions_dict[server] = client_session
        return cls(sessions_dict, recorder)

    async def list_tools(self) -> dict[str, ToolSpec]:
        """Return every tool keyed by `server.tool`, listing each server once."""
        if self._specs_dict:
            return self._specs_dict
        for server, client_session in self._sessions_dict.items():
            response = await client_session.list_tools()
            for tool in response.tools:
                spec = ToolSpec(server, tool.name, tool.description or "", tool.inputSchema)
                self._specs_dict[spec.qualified_name] = spec
        return self._specs_dict

    async def resolve(self, name: str) -> ToolSpec:
        """Resolve `server.tool` or `server__tool` to a ToolSpec."""
        specs_dict = await self.list_tools()
        qualified = name.replace(FUNCTION_SEPARATOR, ".", 1)
        if qualified not in specs_dict:
            raise ToolCallError(f"unknown tool {name}")
        return specs_dict[qualified]

    async def openai_tools(self, qualified_list: list[str]) -> list[dict[str, Any]]:
        """Return OpenAI tool definitions for the given `server.tool` names."""
        return [(await self.resolve(name)).openai_schema() for name in qualified_list]

    async def call(
        self, name: str, arguments: dict[str, Any], run_id: str, step_id: str
    ) -> dict[str, Any]:
        """
        Call a tool, injecting an idempotency key for writes.

        Parameters
        ----------
        name : str
            `server.tool` or `server__tool`.
        arguments : dict
            Tool arguments without the idempotency key.
        run_id, step_id : str
            Sent as request `_meta` and hashed into the idempotency key.

        Returns
        -------
        result : dict
            Structured tool result.

        Raises
        ------
        ToolCallError
            When the server reports an error.
        """
        spec = await self.resolve(name)
        clean_dict = {k: v for k, v in arguments.items() if k != IDEMPOTENCY_PARAM}
        call_dict = dict(clean_dict)
        key = None
        if spec.o_write:
            key = idempotency_key(run_id, step_id, clean_dict)
            call_dict[IDEMPOTENCY_PARAM] = key
        started = time.monotonic()
        result = await self._sessions_dict[spec.server].call_tool(
            spec.name, call_dict, meta={"run_id": run_id, "step_id": step_id}
        )
        latency_s = time.monotonic() - started
        record = ToolCallRecord(run_id, step_id, spec.server, spec.name, "ok", latency_s, key)
        if result.isError:
            message = "".join(getattr(block, "text", "") for block in result.content)
            record.status, record.error = "error", message[:2000]
            self._record(record)
            raise ToolCallError(message)
        record.result = parse_tool_result(result)
        self._record(record)
        return record.result

    def _record(self, record: ToolCallRecord) -> None:
        """Persist a call when a recorder is configured."""
        if self._recorder is not None:
            self._recorder.record(record)
