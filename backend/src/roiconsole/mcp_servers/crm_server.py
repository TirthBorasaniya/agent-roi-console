"""crm-mcp: leads, contacts, activities, and opportunities on Twenty (or the simulated CRM)."""

# ============= Standard Library =============
from typing import Any

# ============= Third-Party =============
from mcp.server.fastmcp import Context, FastMCP

# ============= Local =============
from roiconsole.mcp_servers.backends.factory import ConnectorBackends
from roiconsole.mcp_servers.common import call_context, idempotent_write, logged_read
from roiconsole.mcp_servers.server_base import new_server, run_server_cli

# ============= Constants =============
SERVER_NAME = "crm-mcp"
DEFAULT_PORT = 8103
INSTRUCTIONS = (
    "CRM tools over the sandbox records of the Twenty workspace. Opportunity stages are "
    "NEW, SCREENING, MEETING, PROPOSAL, and CUSTOMER; omit stage to list every open stage."
)


# ============= Server =============


def create_server(
    backends: ConnectorBackends, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> FastMCP:
    """
    Build the crm-mcp server over the given backends.

    Returns
    -------
    server : FastMCP
        Server with list_new_leads, get_contact, update_contact, log_activity,
        and list_opportunities.
    """
    mcp = new_server(SERVER_NAME, INSTRUCTIONS, host, port)
    crm = backends.crm

    @mcp.tool()
    def list_new_leads(since: str, ctx: Context | None = None) -> dict[str, Any]:
        """List contacts created at or after an ISO 8601 timestamp."""
        return logged_read(
            SERVER_NAME, "list_new_leads", call_context(ctx), lambda: crm.list_new_leads(since)
        )

    @mcp.tool()
    def get_contact(id: str, ctx: Context | None = None) -> dict[str, Any]:
        """Return one contact by id."""
        return logged_read(
            SERVER_NAME, "get_contact", call_context(ctx), lambda: crm.get_contact(id)
        )

    @mcp.tool()
    def update_contact(
        id: str, fields: dict[str, Any], idempotency_key: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        """Update a contact's job_title or city."""
        context = call_context(ctx)
        return idempotent_write(
            backends.idempotency,
            SERVER_NAME,
            "update_contact",
            context,
            {"id": id, "fields": fields},
            idempotency_key,
            lambda: crm.update_contact(id, fields),
        )

    @mcp.tool()
    def log_activity(
        contact_id: str, note: str, idempotency_key: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        """Log an activity note on a contact."""
        context = call_context(ctx)
        return idempotent_write(
            backends.idempotency,
            SERVER_NAME,
            "log_activity",
            context,
            {"contact_id": contact_id, "note": note},
            idempotency_key,
            lambda: crm.log_activity(contact_id, note, context.run_id),
        )

    @mcp.tool()
    def list_opportunities(stage: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
        """List opportunities in one stage, or in every open stage when stage is omitted."""
        return logged_read(
            SERVER_NAME,
            "list_opportunities",
            call_context(ctx),
            lambda: crm.list_opportunities(stage),
        )

    return mcp


if __name__ == "__main__":
    run_server_cli(SERVER_NAME, DEFAULT_PORT, create_server)
