"""Shared path types: execution context, step traces, results, and LLM and tool step helpers."""

# ============= Standard Library =============
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# ============= Local =============
from roiconsole.runner.llm import CallMeta, LlmCallError, LlmClient, LlmResult
from roiconsole.runner.mcp_client import McpToolbox, ToolCallError
from roiconsole.workflows.schema import WorkflowDefinition

# ============= Constants =============
MAX_TOOL_RESULT_CHARS = 6000
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


# ============= Types =============


class PathError(RuntimeError):
    """
    A path stopped before finishing.

    Parameters
    ----------
    reason : str
        Short failure reason recorded on the run, e.g. budget_exhausted,
        tool_error, invalid_json, invalid_plan, max_iterations.
    message : str
        Description.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class StepTrace:
    """One executed step, persisted to run_steps."""

    step_id: str
    kind: str
    name: str
    status: str
    latency_s: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PathContext:
    """
    Everything a path needs for one run.

    Attributes
    ----------
    workflow : WorkflowDefinition
        Workflow being executed.
    inputs : dict
        Task inputs with refs resolved.
    instruction : str
        Natural-language task statement for the planner and ReAct paths.
    run_id : str
        Run id sent with every model and tool call.
    model : str
        "auto" for routed calls or a gateway model id for a single-model run.
    llm : LlmClient
        Gateway client.
    toolbox : McpToolbox
        MCP client to the connector servers.
    o_cache : bool
        Whether gateway caching may serve this run.
    """

    workflow: WorkflowDefinition
    inputs: dict[str, Any]
    instruction: str
    run_id: str
    model: str
    llm: LlmClient
    toolbox: McpToolbox
    o_cache: bool = True
    traces: list[StepTrace] = field(default_factory=list)
    llm_results: list[LlmResult] = field(default_factory=list)


@dataclass
class PathResult:
    """Outcome of running a path."""

    status: str
    failure_reason: str | None
    traces: list[StepTrace]
    llm_results: list[LlmResult]

    @property
    def llm_cost_usd(self) -> float:
        """Total model cost of the run."""
        return sum(r.cost_usd for r in self.llm_results)

    @property
    def input_tokens(self) -> int:
        """Total prompt tokens."""
        return sum(r.input_tokens for r in self.llm_results)

    @property
    def output_tokens(self) -> int:
        """Total completion tokens."""
        return sum(r.output_tokens for r in self.llm_results)


class ExecutionPath(Protocol):
    """An execution path."""

    name: str

    async def run(self, ctx: PathContext) -> PathResult:
        """Execute the workflow for one task."""
        ...


# ============= Step Helpers =============


async def llm_step(
    ctx: PathContext,
    step_id: str,
    step_class: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    tools: list[dict[str, Any]] | None = None,
    o_json: bool = False,
) -> LlmResult:
    """
    Call the gateway for one step and trace it.

    Raises
    ------
    PathError
        With the gateway error type as the reason (budget_exhausted halts the run).
    """
    started = time.monotonic()
    meta = CallMeta(ctx.workflow.workflow_id, ctx.run_id, step_id, step_class, ctx.o_cache)
    try:
        result = await ctx.llm.chat(
            ctx.model,
            messages,
            meta,
            max_tokens=max_tokens,
            tools=tools,
            response_format={"type": "json_object"} if o_json else None,
        )
    except LlmCallError as exc:
        ctx.traces.append(
            StepTrace(
                step_id,
                "llm",
                step_class,
                STATUS_FAILED,
                time.monotonic() - started,
                {"error": exc.code},
            )
        )
        raise PathError(exc.code, str(exc)) from exc
    ctx.llm_results.append(result)
    ctx.traces.append(
        StepTrace(
            step_id,
            "llm",
            step_class,
            STATUS_COMPLETED,
            time.monotonic() - started,
            {
                "model": result.model,
                "cost_usd": result.cost_usd,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cache": result.cache,
                "fallback_depth": result.fallback_depth,
            },
        )
    )
    return result


async def tool_step(
    ctx: PathContext, step_id: str, tool: str, args_dict: dict[str, Any]
) -> dict[str, Any]:
    """
    Call an MCP tool for one step and trace it.

    Raises
    ------
    PathError
        reason tool_error when the server rejects the call or the tool is not allowed.
    """
    started = time.monotonic()
    qualified = tool.replace("__", ".", 1)
    if qualified not in ctx.workflow.tool_list:
        ctx.traces.append(
            StepTrace(step_id, "tool", qualified, STATUS_FAILED, 0.0, {"error": "tool not allowed"})
        )
        raise PathError(
            "tool_not_allowed", f"{qualified} is not a tool of {ctx.workflow.workflow_id}"
        )
    try:
        result = await ctx.toolbox.call(qualified, args_dict, ctx.run_id, step_id)
    except ToolCallError as exc:
        ctx.traces.append(
            StepTrace(
                step_id,
                "tool",
                qualified,
                STATUS_FAILED,
                time.monotonic() - started,
                {"error": str(exc)[:500]},
            )
        )
        raise PathError("tool_error", str(exc)) from exc
    ctx.traces.append(
        StepTrace(
            step_id,
            "tool",
            qualified,
            STATUS_COMPLETED,
            time.monotonic() - started,
            {"replay": result.get("idempotent_replay", False)},
        )
    )
    return result


def truncate_json(value: Any, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Serialize a tool result for a prompt, truncated to a character limit."""
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


def finish(ctx: PathContext, error: PathError | None) -> PathResult:
    """Build the PathResult from the context."""
    return PathResult(
        status=STATUS_FAILED if error else STATUS_COMPLETED,
        failure_reason=error.reason if error else None,
        traces=ctx.traces,
        llm_results=ctx.llm_results,
    )
