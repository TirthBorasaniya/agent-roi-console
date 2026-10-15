"""Unit tests for E1 and E2 harnesses with the oracle model, checkpoints, stats, and step checks."""

# ============= Standard Library =============
import json
from contextlib import AsyncExitStack

# ============= Third-Party =============
import pytest
import yaml

# ============= Local =============
from roiconsole.common.db import init_db
from roiconsole.common.results import read_results, write_results
from roiconsole.evaluation import e1_steps, e2_tasks
from roiconsole.evaluation.checkpoint import Checkpoint
from roiconsole.evaluation.e2_tasks import aggregate
from roiconsole.evaluation.routing_table import build_routing_table
from roiconsole.evaluation.stats import percentile, wilson_interval
from roiconsole.evaluation.step_checks import run_check
from roiconsole.gateway.pricing import PriceTable
from roiconsole.gateway.router import plan_route
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.runner.mcp_client import McpToolbox
from roiconsole.runner.runner import TaskRunner
from roiconsole.workflows.registry import load_tasks
from tests.oracle import OracleLlm

pytestmark = pytest.mark.unit

MODEL = "groq/openai/gpt-oss-20b"
TASK_IDS = ["slack_digest_001", "notion_search_001", "meeting_notes_001", "crm_pipeline_001"]


# ============= Helpers =============


class TaskAwareOracle:
    """Routes each call to the oracle of the task currently being run."""

    def __init__(self) -> None:
        self.current: OracleLlm | None = None
        self.n_calls = 0

    async def chat(self, model, messages, meta, **kwargs):
        self.n_calls += 1
        return await self.current.chat(model, messages, meta, **kwargs)


def write_config(tmp_path, name: str, data: dict):
    """Write an eval config with version 1."""
    path = tmp_path / name
    path.write_text(yaml.safe_dump({"version": 1, **data}))
    return path


@pytest.fixture
def tasks(monkeypatch):
    """Restrict the splits to four tasks and point the oracle at the active task."""
    tasks_dict = load_tasks()
    task_list = [tasks_dict[t] for t in TASK_IDS]
    return task_list


async def run_with_runner(tmp_path, fn):
    """Build a runner over in-memory MCP servers and the task-aware oracle."""
    init_db()
    backends = build_backends()
    oracle = TaskAwareOracle()
    original_reset = backends.reset

    def reset(fixtures):
        task = next(t for t in load_tasks().values() if t.fixtures == fixtures)
        oracle.current = OracleLlm(task)
        return original_reset(fixtures)

    backends.reset = reset
    async with AsyncExitStack() as stack:
        toolbox = await McpToolbox.in_memory(stack, backends)
        return await fn(TaskRunner(backends, toolbox, oracle)), oracle


# ============= E1 and Routing Table =============


async def test_e1_measures_every_step_class_and_builds_a_routing_table(
    tmp_path, monkeypatch, tasks
):
    monkeypatch.setattr(e1_steps, "tasks_for", lambda split: tasks)
    config = write_config(
        tmp_path,
        "e1.yaml",
        {
            "split": "dev",
            "repeats": 1,
            "models": [MODEL, "fake/unpriced"],
            "agent_step_class": "agent_tool_use",
            "planning_step_class": "planning",
            "checkpoint": str(tmp_path / "e1.jsonl"),
        },
    )
    (metrics, records, _), _ = await run_with_runner(tmp_path, lambda r: e1_steps.run_e1(r, config))
    assert metrics["models_skipped"] == {"fake/unpriced": "no price"}
    classes = metrics["step_classes"]
    assert set(classes) == {
        "summarization",
        "extraction",
        "classification",
        "generation_short",
        "planning",
        "agent_tool_use",
    }
    for step_class, models in classes.items():
        assert models[MODEL]["success"]["rate"] == 1.0, (
            step_class,
            [r for r in records if not r["success"]],
        )
    path = write_results("e1_step_success", "e1", config, metrics, "", {"records": records})
    table = build_routing_table(read_results("e1_step_success"), path, PriceTable.load())
    assert plan_route(table, "extraction", 0.9).models == [MODEL]
    assert table.metadata_dict["source_sha256"]


# ============= E2 and Checkpoints =============


async def test_e2_runs_resume_from_checkpoint(tmp_path, monkeypatch, tasks):
    monkeypatch.setattr(e2_tasks, "tasks_for", lambda split: tasks[:2])
    config = write_config(
        tmp_path,
        "e2.yaml",
        {
            "split": "heldout",
            "repeats": 1,
            "paths": ["pipeline", "react"],
            "model_configs": ["auto", MODEL],
            "checkpoint": str(tmp_path / "e2.jsonl"),
        },
    )
    monkeypatch.setattr(e2_tasks, "PriceTable", PriceTable)
    (metrics, records, _), oracle = await run_with_runner(
        tmp_path, lambda r: e2_tasks.run_e2(r, config)
    )
    assert metrics["n_runs"] == 2 * 2 * 2 == len(records)
    assert metrics["by_path_and_config"]["pipeline"][MODEL]["success"]["rate"] == 1.0
    assert metrics["routed_model_share"] == {"fake/oracle": 1.0}
    (metrics_again, _, _), oracle_again = await run_with_runner(
        tmp_path, lambda r: e2_tasks.run_e2(r, config)
    )
    assert oracle_again.n_calls == 0 and metrics_again["n_runs"] == metrics["n_runs"]


def test_checkpoint_skips_truncated_line(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps({"a": "1", "v": 1}) + "\n" + '{"a": "2", "v"')
    checkpoint = Checkpoint(path, ("a",))
    assert checkpoint.done(("1",)) and not checkpoint.done(("2",))


def test_aggregate_cost_per_success_and_comparison():
    def record(config, success, cost):
        return {
            "path": "pipeline",
            "model_config": config,
            "workflow_id": "wf",
            "success": success,
            "llm_cost_usd": cost,
            "tool_cost_usd": 0.0,
            "latency_s": 1.0,
            "failure_reason": None if success else "x",
            "models_used": {"m1": 1} if config == "auto" else {},
        }

    record_list = [
        record("auto", True, 0.01),
        record("auto", True, 0.01),
        record("m", True, 0.04),
        record("m", False, 0.04),
    ]
    result = aggregate(record_list)
    assert result["by_path_and_config"]["pipeline"]["auto"][
        "cost_per_successful_task_usd"
    ] == pytest.approx(0.01)
    assert result["by_path_and_config"]["pipeline"]["m"][
        "cost_per_successful_task_usd"
    ] == pytest.approx(0.08)
    assert result["routed_vs_single_model"]["pipeline"]["m"][
        "cost_per_success_ratio"
    ] == pytest.approx(0.125)


# ============= Stats and Step Checks =============


def test_wilson_interval_known_values():
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-3) and high == pytest.approx(0.9433, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([], 0.5) is None


def test_step_checks():
    assert run_check("json_field_equals", {"tag": "Billing"}, {"field": "tag", "value": "billing"})[
        0
    ]
    assert not run_check("json_field_equals", "text", {"field": "tag", "value": "x"})[0]
    assert run_check(
        "json_list_field_equals",
        {"a": [{"o": "Maya"}, {"o": "Omar"}]},
        {"field": "a", "key": "o", "value": ["omar", "maya"]},
    )[0]
    assert run_check("text_mentions_amount", "total $1,250", {"amount": 1250})[0]
    assert not run_check(
        "text_excludes_terms", "see Invoice Schedule", {"terms": ["invoice schedule"]}
    )[0]
