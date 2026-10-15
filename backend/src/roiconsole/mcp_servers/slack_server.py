"""slack-mcp: post messages, list allowlisted channels, and read threads."""

# ============= Standard Library =============
from typing import Any

# ============= Third-Party =============
from mcp.server.fastmcp import Context, FastMCP

# ============= Local =============
from roiconsole.mcp_servers.backends.factory import ConnectorBackends
from roiconsole.mcp_servers.common import call_context, idempotent_write, logged_read
from roiconsole.mcp_servers.server_base import new_server, run_server_cli

# ============= Constants =============
SERVER_NAME = "slack-mcp"
DEFAULT_PORT = 8101
INSTRUCTIONS = "Slack tools limited to the console's allowlisted sandbox channels."


# ============= Server =============


def create_server(
    backends: ConnectorBackends, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> FastMCP:
    """
    Build the slack-mcp server over the given backends.

    Parameters
    ----------
    backends : ConnectorBackends
        Backends for the current mode; only `slack` is used.
    host, port : str, int
        Bind address for streamable HTTP.

    Returns
    -------
    server : FastMCP
        Server with post_message, list_channels, and read_thread.
    """
    mcp = new_server(SERVER_NAME, INSTRUCTIONS, host, port)
    slack = backends.slack

    @mcp.tool()
    def post_message(
        channel: str,
        text: str,
        thread_ts: str | None = None,
        idempotency_key: str = "",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Post a message to an allowlisted channel, optionally as a reply in a thread."""
        context = call_context(ctx)
        arguments = {"channel": channel, "text": text, "thread_ts": thread_ts}
        return idempotent_write(
            backends.idempotency,
            SERVER_NAME,
            "post_message",
            context,
            arguments,
            idempotency_key,
            lambda: slack.post_message(channel, text, thread_ts, context.run_id),
        )

    @mcp.tool()
    def list_channels(ctx: Context | None = None) -> dict[str, Any]:
        """List the allowlisted channels the console may read and write."""
        return logged_read(SERVER_NAME, "list_channels", call_context(ctx), slack.list_channels)

    @mcp.tool()
    def read_thread(channel: str, thread_ts: str, ctx: Context | None = None) -> dict[str, Any]:
        """Read a thread's parent message and replies, oldest first."""
        return logged_read(
            SERVER_NAME,
            "read_thread",
            call_context(ctx),
            lambda: slack.read_thread(channel, thread_ts),
        )

    return mcp


if __name__ == "__main__":
    run_server_cli(SERVER_NAME, DEFAULT_PORT, create_server)
