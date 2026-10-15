"""Unit tests: every workflow on every path over sandbox MCP servers, plus failure handling."""

# ============= Standard Library =============
import json
from contextlib import AsyncExitStack

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.db import connect, init_db
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.paths.base import PathError
from roiconsole.paths.planner_executor import resolve_placeholders, validate_plan
from roiconsole.runner.llm import LlmCallError, parse_json_output
from roiconsole.runner.mcp_client import ToolCallRecorder
from roiconsole.runner.runner import TaskRunner
from roiconsole.workflows.registry import load_tasks
from tests.helpers import in_memory_toolbox
from tests.oracle import OracleLlm

pytestmark = pytest.mark.unit

TASK_IDS = [
    "slack_digest_001",
    "notion_search_001",
    "meeting_notes_001",
    "crm_pipeline_001",
    "crm_pipeline_002",
]
PATHS = ["pipeline", "planner_executor", "react"]


# ============= Helpers =============


async def run_with(llm, task, path_name, model="auto"):
    """Run one task through in-memory MCP servers and return the outcome."""
    init_db()
    backends = build_backends()
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends, ToolCallRecorder())
        return await TaskRunner(backends, toolbox, llm).run(task, path_name, model)


# ============= End-to-End on Sandbox =============


@pytest.mark.parametrize("path_name", PATHS)
@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_correct_answers_pass_validators_on_every_path(task_id, path_name):
    task = load_tasks()[task_id]
    outcome = await run_with(OracleLlm(task), task, path_name)
    assert outcome.success, (outcome.failure_reason, outcome.validator_results)


async def test_run_is_persisted_with_steps():
    task = load_tasks()["crm_pipeline_001"]
    outcome = await run_with(OracleLlm(task), task, "pipeline")
    with connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (outcome.run_id,)).fetchone()
        step_ids = [
            r["step_id"]
            for r in conn.execute(
                "SELECT step_id FROM run_steps WHERE run_id = ?", (outcome.run_id,)
            )
        ]
        tools = [
            r["tool"]
            for r in conn.execute("SELECT tool FROM tool_calls WHERE run_id = ?", (outcome.run_id,))
        ]
    assert run["status"] == "COMPLETED" and run["success"] == 1
    assert step_ids == [
        "fetch_opportunities",
        "write_summary",
        "pick_followup",
        "post_summary",
        "log_followup",
    ]
    assert tools == ["list_opportunities", "post_message", "log_activity"]


class WrongDigestLlm(OracleLlm):
    """Writes a digest that omits the required terms."""

    def digest(self) -> str:
        return "Digest: nothing decided."


@pytest.mark.parametrize("path_name", PATHS)
async def test_wrong_output_fails_validators(path_name):
    task = load_tasks()["slack_digest_001"]
    outcome = await run_with(WrongDigestLlm(task), task, path_name)
    assert not outcome.success and outcome.failure_reason == "validators_failed"


class BudgetHaltLlm(OracleLlm):
    """Raises budget_exhausted on the second call."""

    async def chat(self, model, messages, meta, **kwargs):
        if len(self.calls) >= 1:
            raise LlmCallError("budget_exhausted", "no budget")
        return await super().chat(model, messages, meta, **kwargs)


async def test_budget_exhausted_is_a_failed_task():
    task = load_tasks()["crm_pipeline_001"]
    outcome = await run_with(BudgetHaltLlm(task), task, "pipeline")
    assert not outcome.success and outcome.failure_reason == "budget_exhausted"


class BadJsonLlm(OracleLlm):
    """Returns prose where JSON is required."""

    async def chat(self, model, messages, meta, **kwargs):
        if meta.step_id == "extract_meeting":
            self.calls.append(meta)
            from tests.oracle import _result

            return _result("I could not parse that.")
        return await super().chat(model, messages, meta, **kwargs)


async def test_invalid_json_fails_pipeline():
    task = load_tasks()["meeting_notes_001"]
    outcome = await run_with(BadJsonLlm(task), task, "pipeline")
    assert outcome.failure_reason == "invalid_json"


# ============= Planner Helpers =============


def test_validate_plan_rejects_disallowed_tools_and_forward_references():
    allowed = ["slack.post_message"]
    with pytest.raises(PathError, match="not allowed"):
        validate_plan(
            {"steps": [{"id": "s1", "action": "tool", "tool": "crm.log_activity", "args": {}}]},
            allowed,
        )
    with pytest.raises(PathError, match="later or unknown"):
        validate_plan(
            {
                "steps": [
                    {
                        "id": "s1",
                        "action": "tool",
                        "tool": "slack.post_message",
                        "args": {"text": "{{s2}}"},
                    }
                ]
            },
            allowed,
        )
    with pytest.raises(PathError):
        validate_plan({"steps": []}, allowed)


def test_resolve_placeholders_keeps_native_values_and_interpolates_strings():
    outputs = {"s1": {"page_id": "p1", "items": [1, 2]}, "s2": "hello"}
    assert resolve_placeholders("{{s1.items}}", outputs) == [1, 2]
    assert resolve_placeholders({"t": "say {{s2}} to {{s1.page_id}}"}, outputs) == {
        "t": "say hello to p1"
    }
    with pytest.raises(PathError):
        resolve_placeholders("{{s1.missing}}", outputs)


def test_parse_json_output_tolerates_fences_and_prose():
    assert parse_json_output('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_output('Here you go: {"a": 1} thanks') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_output("no json")
    assert json.dumps(parse_json_output('{"t": "x"}')) == '{"t": "x"}'
