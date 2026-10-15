"""Test helpers: in-memory MCP toolboxes over sandbox backends."""

# ============= Standard Library =============
from contextlib import AsyncExitStack

# ============= Local =============
from roiconsole.mcp_servers.backends.factory import ConnectorBackends
from roiconsole.runner.mcp_client import McpToolbox, ToolCallRecorder

# ============= Helpers =============


async def in_memory_toolbox(
    stack: AsyncExitStack,
    backends: ConnectorBackends,
    recorder: ToolCallRecorder | None = None,
) -> McpToolbox:
    """Connect a toolbox to the three servers through in-memory MCP sessions."""
    return await McpToolbox.in_memory(stack, backends, recorder)
