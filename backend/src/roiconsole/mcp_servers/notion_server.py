"""notion-mcp: create pages, query databases, and append blocks in allowlisted databases."""

# ============= Standard Library =============
from typing import Any

# ============= Third-Party =============
from mcp.server.fastmcp import Context, FastMCP

# ============= Local =============
from roiconsole.mcp_servers.backends.factory import ConnectorBackends
from roiconsole.mcp_servers.common import call_context, idempotent_write, logged_read
from roiconsole.mcp_servers.server_base import new_server, run_server_cli

# ============= Constants =============
SERVER_NAME = "notion-mcp"
DEFAULT_PORT = 8102
INSTRUCTIONS = (
    "Notion tools limited to the console's allowlisted databases. Properties use simplified "
    "values: multi_select as a list of names, rich_text as a string, date as YYYY-MM-DD. "
    "Blocks use {type, text}, with type heading_2, paragraph, bulleted_list_item, or to_do."
)


# ============= Server =============


def create_server(
    backends: ConnectorBackends, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> FastMCP:
    """
    Build the notion-mcp server over the given backends.

    Returns
    -------
    server : FastMCP
        Server with create_page, query_database, and append_blocks.
    """
    mcp = new_server(SERVER_NAME, INSTRUCTIONS, host, port)
    notion = backends.notion

    @mcp.tool()
    def create_page(
        parent_id: str,
        title: str,
        properties: dict[str, Any] | None = None,
        idempotency_key: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Create a page in an allowlisted database (parent_id is the database name or id)."""
        context = call_context(ctx)
        arguments = {"parent_id": parent_id, "title": title, "properties": properties or {}}
        return idempotent_write(
            backends.idempotency,
            SERVER_NAME,
            "create_page",
            context,
            arguments,
            idempotency_key,
            lambda: notion.create_page(parent_id, title, properties or {}, context.run_id),
        )

    @mcp.tool()
    def query_database(
        database_id: str, filter: dict[str, Any] | None = None, ctx: Context | None = None
    ) -> dict[str, Any]:
        """Query an allowlisted database with a Notion filter object."""
        return logged_read(
            SERVER_NAME,
            "query_database",
            call_context(ctx),
            lambda: notion.query_database(database_id, filter),
        )

    @mcp.tool()
    def append_blocks(
        page_id: str,
        blocks: list[dict[str, Any]],
        idempotency_key: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Append simplified blocks to a page in an allowlisted database."""
        context = call_context(ctx)
        arguments = {"page_id": page_id, "blocks": blocks}
        return idempotent_write(
            backends.idempotency,
            SERVER_NAME,
            "append_blocks",
            context,
            arguments,
            idempotency_key,
            lambda: notion.append_blocks(page_id, blocks),
        )

    return mcp


if __name__ == "__main__":
    run_server_cli(SERVER_NAME, DEFAULT_PORT, create_server)
