"""Shared FastMCP server construction and command-line entry point."""

# ============= Standard Library =============
import argparse
import logging
from collections.abc import Callable

# ============= Third-Party =============
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# ============= Local =============
from roiconsole.common.logging import configure_logging
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.factory import ConnectorBackends, build_backends

# ============= Constants =============
logger = logging.getLogger(__name__)
TRANSPORTS = ("stdio", "streamable-http")


# ============= Construction =============


def new_server(name: str, instructions: str, host: str, port: int) -> FastMCP:
    """
    Create a FastMCP server.

    DNS-rebinding protection stays on for loopback binds; inside compose the
    server binds 0.0.0.0 on a private network and accepts the service hostname.
    """
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    if host in ("127.0.0.1", "localhost"):
        security = None
    return FastMCP(
        name=name,
        instructions=instructions,
        host=host,
        port=port,
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )


def run_server_cli(
    service: str,
    default_port: int,
    factory: Callable[[ConnectorBackends, str, int], FastMCP],
) -> None:
    """
    Parse --transport, --host, --port and run a server.

    Parameters
    ----------
    service : str
        Service name for logs.
    default_port : int
        Port used for streamable HTTP when --port is absent.
    factory : callable
        Builds the FastMCP server from backends, host, and port.
    """
    parser = argparse.ArgumentParser(description=f"{service} MCP server")
    parser.add_argument("--transport", choices=TRANSPORTS, default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()
    # stdout carries the stdio protocol, so logs go to stderr
    configure_logging(service, o_stderr=True)
    backends = build_backends(get_settings())
    logger.info("starting", extra={"mode": backends.mode, "transport": args.transport})
    factory(backends, args.host, args.port).run(transport=args.transport)
