"""Integration test: each workflow once per path through MCP server processes on fixtures."""

# ============= Standard Library =============
import os
from contextlib import AsyncExitStack

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.db import init_db
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.runner.mcp_client import McpToolbox, ToolCallRecorder
from roiconsole.runner.runner import TaskRunner
from roiconsole.workflows.registry import load_workflows, tasks_for
from tests.oracle import OracleLlm

pytestmark = pytest.mark.integration

PATHS = ["pipeline", "planner_executor", "react"]


# ============= Tests =============


@pytest.mark.parametrize("path_name", PATHS)
@pytest.mark.parametrize("workflow_id", sorted(load_workflows()))
async def test_workflow_passes_validators_over_mcp_processes(workflow_id, path_name, monkeypatch):
    """
    Servers run as stdio subprocesses by default. Inside compose, set
    ROI_IT_MCP=http with SLACK_MCP_URL, NOTION_MCP_URL, and CRM_MCP_URL so the
    test drives the compose services over streamable HTTP on the shared volume.
    """
    if os.environ.get("ROI_IT_MCP") == "http":
        for name in ("ROI_DATA_DIR", "SLACK_MCP_URL", "NOTION_MCP_URL", "CRM_MCP_URL"):
            monkeypatch.setenv(name, os.environ[name])
        get_settings.cache_clear()
    init_db()
    task = tasks_for("dev", workflow_id)[0]
    backends = build_backends()
    async with AsyncExitStack() as stack:
        toolbox = await McpToolbox.connect(stack, get_settings(), ToolCallRecorder())
        outcome = await TaskRunner(backends, toolbox, OracleLlm(task)).run(task, path_name)
    assert outcome.success, (outcome.failure_reason, outcome.validator_results)
