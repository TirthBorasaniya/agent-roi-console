"""Integration test: the three MCP servers as stdio subprocesses on dry-run fixtures."""

# ============= Standard Library =============
from contextlib import AsyncExitStack

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.runner.mcp_client import McpToolbox
from roiconsole.workflows.registry import load_tasks

pytestmark = pytest.mark.integration


# ============= Tests =============


async def test_stdio_servers_share_sandbox_state_with_the_runner():
    backends = build_backends()
    task = load_tasks()["crm_pipeline_001"]
    refs_dict = backends.reset(task.fixtures)
    async with AsyncExitStack() as stack:
        toolbox = await McpToolbox.connect(stack, get_settings())
        assert len(await toolbox.list_tools()) == 11
        result = await toolbox.call("crm.list_opportunities", {"stage": None}, "run-int", "fetch")
        assert result["opportunities"]
        await toolbox.call(
            "slack.post_message",
            {"channel": "roi-sandbox-pipeline", "text": "pipeline summary"},
            "run-int",
            "post",
        )
        await toolbox.call(
            "crm.log_activity",
            {"contact_id": refs_dict["p_1"], "note": "pipeline"},
            "run-int",
            "log",
        )
    snapshot = backends.snapshot(0.0)
    assert [m.text for m in snapshot.slack_messages if m.o_bot] == ["pipeline summary"]
    assert [a.contact_id for a in snapshot.crm_activities] == [refs_dict["p_1"]]
