"""Test helpers: in-memory MCP toolboxes over sandbox backends."""

# ============= Standard Library =============
from contextlib import AsyncExitStack

# ============= Third-Party =============
from mcp.shared.memory import create_connected_server_and_client_session

# ============= Local =============
from roiconsole.mcp_servers import crm_server, notion_server, slack_server
from roiconsole.mcp_servers.backends.factory import ConnectorBackends
from roiconsole.runner.mcp_client import McpToolbox, ToolCallRecorder

# ============= Constants =============
SERVER_FACTORIES = {
    "slack": slack_server.create_server,
    "notion": notion_server.create_server,
    "crm": crm_server.create_server,
}


# ============= Helpers =============


async def in_memory_toolbox(
    stack: AsyncExitStack,
    backends: ConnectorBackends,
    recorder: ToolCallRecorder | None = None,
) -> McpToolbox:
    """Connect a toolbox to the three servers through in-memory MCP sessions."""
    sessions_dict = {}
    for server, factory in SERVER_FACTORIES.items():
        mcp = factory(backends)
        sessions_dict[server] = await stack.enter_async_context(
            create_connected_server_and_client_session(mcp._mcp_server)
        )
    return McpToolbox(sessions_dict, recorder)
