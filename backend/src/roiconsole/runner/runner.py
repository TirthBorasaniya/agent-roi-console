"""Run one task end to end: reset the workspace, execute a path, validate end state, persist."""

# ============= Standard Library =============
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

# ============= Local =============
from roiconsole.common import metrics
from roiconsole.common.db import insert_row, session, utc_now
from roiconsole.common.ids import new_run_id
from roiconsole.mcp_servers.backends.factory import ConnectorBackends
from roiconsole.paths.base import PathContext
from roiconsole.paths.registry import get_path
from roiconsole.runner.llm import LlmClient
from roiconsole.runner.mcp_client import McpToolbox
from roiconsole.sandbox.fixtures import resolve_inputs
from roiconsole.validators.engine import all_passed, run_validators
from roiconsole.workflows.registry import get_workflow
from roiconsole.workflows.schema import TaskDefinition
from roiconsole.workflows.templating import render_value

# ============= Constants =============
logger = logging.getLogger(__name__)


# ============= Types =============


@dataclass
class RunOutcome:
    """Everything measured about one run."""

    run_id: str
    task_id: str
    workflow_id: str
    path: str
    model_config: str
    success: bool
    failure_reason: str | None
    validator_results: list[dict[str, Any]]
    llm_cost_usd: float
    tool_cost_usd: float
    input_tokens: int
    output_tokens: int
    latency_s: float
    models_used: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for checkpoints and results files."""
        return dict(self.__dict__)


# ============= Runner =============


class TaskRunner:
    """
    Execute tasks against one set of backends, one MCP toolbox, and one gateway client.

    Runs are sequential: a reset replaces the whole sandbox workspace.

    Parameters
    ----------
    backends : ConnectorBackends
        Used for resets and end-state snapshots (the MCP servers use the same mode).
    toolbox : McpToolbox
        MCP client the paths call.
    llm : LlmClient
        Gateway client.
    o_persist : bool
        Write runs and run_steps rows to the console database.
    """

    def __init__(
        self,
        backends: ConnectorBackends,
        toolbox: McpToolbox,
        llm: LlmClient,
        o_persist: bool = True,
    ) -> None:
        self.backends = backends
        self.toolbox = toolbox
        self.llm = llm
        self.o_persist = o_persist

    async def run(
        self,
        task: TaskDefinition,
        path_name: str,
        model: str = "auto",
        trigger: str = "eval",
        o_cache: bool = False,
        run_id: str | None = None,
    ) -> RunOutcome:
        """
        Run one task on one path with one model configuration.

        Parameters
        ----------
        task : TaskDefinition
            Task with fixtures and expected end state.
        path_name : str
            pipeline, planner_executor, or react.
        model : str
            "auto" (routed) or a gateway model id (single-model configuration).
        trigger : str
            eval, api, live, or guard, recorded on the run.
        o_cache : bool
            Allow gateway caches; evaluations keep them off so every call is measured.
        run_id : str or None
            Explicit id; a new one is generated otherwise.

        Returns
        -------
        outcome : RunOutcome
            Success is true only when the path completed and every validator passed.
        """
        workflow = get_workflow(task.workflow_id)
        run_id = run_id or new_run_id()
        reset_started = time.time()
        refs_dict = self.backends.reset(task.fixtures)
        inputs_dict = resolve_inputs(task, refs_dict)
        instruction = render_value(workflow.agent.instruction_template, {"inputs": inputs_dict})
        started_at = utc_now()
        self._insert_run(run_id, task, path_name, model, trigger, started_at)
        ctx = PathContext(
            workflow, inputs_dict, instruction, run_id, model, self.llm, self.toolbox, o_cache
        )
        started = time.monotonic()
        result = await get_path(path_name).run(ctx)
        latency_s = time.monotonic() - started
        state = self.backends.snapshot(reset_started)
        validator_list = run_validators(task, state, refs_dict, inputs_dict)
        o_success = result.status == "completed" and all_passed(validator_list)
        failure_reason = result.failure_reason
        if not o_success and failure_reason is None:
            failure_reason = "validators_failed"
        models_used: dict[str, int] = {}
        for llm_result in result.llm_results:
            models_used[llm_result.model] = models_used.get(llm_result.model, 0) + 1
        outcome = RunOutcome(
            run_id=run_id,
            task_id=task.task_id,
            workflow_id=task.workflow_id,
            path=path_name,
            model_config=model,
            success=o_success,
            failure_reason=None if o_success else failure_reason,
            validator_results=[v.to_dict() for v in validator_list],
            llm_cost_usd=result.llm_cost_usd,
            tool_cost_usd=workflow.tool_cost_usd_per_task,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_s=latency_s,
            models_used=models_used,
        )
        self._complete_run(outcome, result.traces)
        metrics.RUNS_TOTAL.labels(
            workflow=task.workflow_id, path=path_name, outcome="success" if o_success else "failure"
        ).inc()
        logger.info(
            "run complete",
            extra={
                "run_id": run_id,
                "task_id": task.task_id,
                "path": path_name,
                "success": o_success,
            },
        )
        return outcome

    # ============= Persistence =============

    def _insert_run(
        self,
        run_id: str,
        task: TaskDefinition,
        path: str,
        model: str,
        trigger: str,
        started_at: str,
    ) -> None:
        """Insert the RUNNING row so partial state is visible."""
        if not self.o_persist:
            return
        with session() as conn:
            insert_row(
                conn,
                "runs",
                {
                    "run_id": run_id,
                    "workflow_id": task.workflow_id,
                    "task_id": task.task_id,
                    "path": path,
                    "model_config": model,
                    "trigger": trigger,
                    "status": "RUNNING",
                    "o_demo": int(self.backends.mode == "demo"),
                    "started_at": started_at,
                },
            )

    def _complete_run(self, outcome: RunOutcome, trace_list: list) -> None:
        """Finalize the run row and write step traces."""
        if not self.o_persist:
            return
        with session() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, success = ?, failure_reason = ?, "
                "validator_results = ?, "
                "input_tokens = ?, output_tokens = ?, llm_cost_usd = ?, tool_cost_usd = ?, "
                "latency_s = ?, completed_at = ? WHERE run_id = ?",
                (
                    "COMPLETED" if outcome.success else "FAILED",
                    int(outcome.success),
                    outcome.failure_reason,
                    json.dumps(outcome.validator_results),
                    outcome.input_tokens,
                    outcome.output_tokens,
                    outcome.llm_cost_usd,
                    outcome.tool_cost_usd,
                    outcome.latency_s,
                    utc_now(),
                    outcome.run_id,
                ),
            )
            for trace in trace_list:
                insert_row(
                    conn,
                    "run_steps",
                    {
                        "run_id": outcome.run_id,
                        "step_id": trace.step_id,
                        "kind": trace.kind,
                        "name": trace.name,
                        "status": trace.status,
                        "latency_s": round(trace.latency_s, 4),
                        "detail": trace.detail,
                        "created_at": utc_now(),
                    },
                )
