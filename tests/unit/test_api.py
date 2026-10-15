"""Unit tests for the console API with an oracle-backed runner over in-memory MCP servers."""

# ============= Standard Library =============
from contextlib import AsyncExitStack, asynccontextmanager

# ============= Third-Party =============
import pytest
from fastapi.testclient import TestClient

# ============= Local =============
from roiconsole.api.app import create_app
from roiconsole.api.rate_limit import run_limiter
from roiconsole.guard.service import load_state, save_state
from roiconsole.guard.state_machine import GuardState
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.runner.mcp_client import McpToolbox, ToolCallRecorder
from roiconsole.runner.runner import TaskRunner
from roiconsole.workflows.registry import load_tasks
from tests.oracle import OracleLlm

pytestmark = pytest.mark.unit


# ============= Fixtures =============


class TaskOracle:
    """Answers for whichever task the runner reset last."""

    def __init__(self) -> None:
        self.oracle = None

    async def chat(self, *args, **kwargs):
        return await self.oracle.chat(*args, **kwargs)


def oracle_runner_factory():
    """Return a runner factory over in-memory MCP servers and the oracle."""

    @asynccontextmanager
    async def factory():
        backends = build_backends()
        llm = TaskOracle()
        original_reset = backends.reset

        def reset(fixtures):
            llm.oracle = OracleLlm(next(t for t in load_tasks().values() if t.fixtures == fixtures))
            return original_reset(fixtures)

        backends.reset = reset
        async with AsyncExitStack() as stack:
            toolbox = await McpToolbox.in_memory(stack, backends, ToolCallRecorder())
            yield TaskRunner(backends, toolbox, llm)

    return factory


@pytest.fixture
def client():
    """API client with the oracle runner."""
    run_limiter.reset()
    with TestClient(create_app(oracle_runner_factory())) as test_client:
        yield test_client


# ============= Tests =============


def test_health_meta_and_workflows(client):
    assert client.get("/health").json() == {"status": "ok"}
    meta = client.get("/api/meta").json()
    assert meta["connector_mode"] == "dryrun" and not meta["o_demo"]
    workflows = client.get("/api/workflows").json()
    assert {w["workflow_id"] for w in workflows} == {
        "slack_digest",
        "notion_search",
        "meeting_notes",
        "crm_pipeline",
    }
    assert client.get("/api/workflows/nope").status_code == 404


def test_run_then_roi_runs_and_detail(client):
    response = client.post("/api/workflows/crm_pipeline/run", json={})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["success"] and run["path"] == "pipeline"
    detail = client.get(f"/api/runs/{run['run_id']}").json()
    assert [s["step_id"] for s in detail["steps"]][0] == "fetch_opportunities"
    assert len(detail["tool_calls"]) == 3
    roi = client.get("/api/workflows/crm_pipeline/roi").json()
    assert roi["overall"]["n_runs"] == 1 and roi["overall"]["net_roi_usd"] > 0
    assert len(client.get("/api/workflows/crm_pipeline/sensitivity").json()["grid"]) == 12
    overview = {o["workflow_id"]: o for o in client.get("/api/overview").json()}
    assert overview["crm_pipeline"]["n_runs"] == 1
    assert (
        client.get("/api/runs", params={"workflow_id": "crm_pipeline"}).json()[0]["run_id"]
        == run["run_id"]
    )
    assert client.get("/api/runs/missing").status_code == 404


def test_paused_workflow_refuses_runs_until_resumed(client):
    save_state(GuardState("slack_digest", "pipeline", "paused", 0))
    assert client.post("/api/workflows/slack_digest/run", json={}).status_code == 409
    assert client.post("/api/workflows/slack_digest/resume").json()["guard_phase"] == "normal"
    assert load_state("slack_digest", "pipeline").phase == "normal"


def test_bad_task_and_path_are_rejected(client):
    assert (
        client.post(
            "/api/workflows/slack_digest/run", json={"task_id": "crm_pipeline_001"}
        ).status_code
        == 400
    )
    assert client.post("/api/workflows/slack_digest/run", json={"path": "magic"}).status_code == 400


def test_gateway_stats_guard_log_results_and_metrics(client):
    stats = client.get("/api/gateway/stats").json()
    assert stats["calls"] == 0 and "breakers" in stats
    assert client.get("/api/guard/log").json() == []
    assert client.get("/api/results/e2_tasks").status_code == 404
    assert client.get("/api/results/secrets").status_code == 404
    assert client.post("/api/guard/evaluate").json()["crm_pipeline"]["action"] == "none"
    assert "roiconsole_runs_total" in client.get("/metrics").text


def test_runs_are_rate_limited(client):
    run_limiter.reset()
    original = run_limiter.limit
    run_limiter.limit = 1
    try:
        assert client.post("/api/workflows/slack_digest/run", json={}).status_code == 200
        response = client.post("/api/workflows/slack_digest/run", json={})
        assert response.status_code == 429 and response.headers["Retry-After"]
    finally:
        run_limiter.limit = original
        run_limiter.reset()


def test_runner_unavailable_returns_503():
    with TestClient(create_app(o_start_runner=False)) as test_client:
        assert test_client.post("/api/workflows/slack_digest/run", json={}).status_code == 503
