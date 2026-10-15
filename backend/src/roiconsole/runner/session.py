"""Open a ready TaskRunner: backends, an MCP toolbox, and a gateway client, per configured modes."""

# ============= Standard Library =============
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

# ============= Local =============
from roiconsole.common.db import init_db
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.runner.llm import HttpGatewayClient, InProcessGatewayClient, LlmClient
from roiconsole.runner.mcp_client import McpToolbox, ToolCallRecorder
from roiconsole.runner.runner import TaskRunner

# ============= Constants =============
GATEWAY_MODES = ("inprocess", "http")
MCP_MODES = ("inprocess", "stdio", "http")


# ============= Session =============


def build_llm_client(gateway_mode: str) -> LlmClient:
    """Return an HTTP client to GATEWAY_URL or an in-process gateway built from configs."""
    settings = get_settings()
    if gateway_mode == "http":
        return HttpGatewayClient(settings.gateway_url, settings.gateway_api_key)
    if gateway_mode != "inprocess":
        raise ValueError(f"gateway_mode must be one of {GATEWAY_MODES}")
    from roiconsole.gateway.app import build_service
    from roiconsole.gateway.telemetry import setup_tracing

    setup_tracing(f"{settings.service_name}-gateway", settings.otel_exporter_otlp_endpoint)
    return InProcessGatewayClient(build_service(settings))


@asynccontextmanager
async def runner_session(
    gateway_mode: str = "inprocess", mcp_mode: str = "stdio", o_persist: bool = True
) -> AsyncIterator[TaskRunner]:
    """
    Yield a TaskRunner wired for the configured connector mode.

    Parameters
    ----------
    gateway_mode : str
        inprocess (same code, no HTTP hop) or http (GATEWAY_URL).
    mcp_mode : str
        stdio (servers as subprocesses), http (SLACK_MCP_URL and friends), or
        inprocess (in-memory MCP sessions).
    o_persist : bool
        Write runs, steps, and tool calls to the console database.
    """
    if mcp_mode not in MCP_MODES:
        raise ValueError(f"mcp_mode must be one of {MCP_MODES}")
    settings = get_settings()
    init_db()
    backends = build_backends(settings)
    recorder = ToolCallRecorder() if o_persist else None
    async with AsyncExitStack() as stack:
        if mcp_mode == "inprocess":
            toolbox = await McpToolbox.in_memory(stack, backends, recorder)
        else:
            if mcp_mode == "http" and not (
                settings.slack_mcp_url and settings.notion_mcp_url and settings.crm_mcp_url
            ):
                raise ValueError(
                    "mcp_mode http needs SLACK_MCP_URL, NOTION_MCP_URL, and CRM_MCP_URL"
                )
            toolbox = await McpToolbox.connect(stack, settings, recorder)
        yield TaskRunner(backends, toolbox, build_llm_client(gateway_mode), o_persist=o_persist)
