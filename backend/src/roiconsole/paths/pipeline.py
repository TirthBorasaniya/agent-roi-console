"""Deterministic pipeline path: declared steps in order, with LLM calls only where declared."""

# ============= Standard Library =============
import time
from typing import Any, TypedDict

# ============= Third-Party =============
from langgraph.graph import END, START, StateGraph

# ============= Local =============
from roiconsole.paths.base import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    PathContext,
    PathError,
    PathResult,
    StepTrace,
    finish,
    llm_step,
    tool_step,
)
from roiconsole.runner.llm import parse_json_output
from roiconsole.workflows.schema import StepDefinition
from roiconsole.workflows.templating import TemplateRenderError, render_string, render_value
from roiconsole.workflows.transforms import run_transform

# ============= Constants =============
PATH_NAME = "pipeline"


# ============= State =============


class PipelineState(TypedDict):
    """Graph state: outputs by step id and the first error, if any."""

    steps: dict[str, dict[str, Any]]
    error: PathError | None


# ============= Step Execution =============


def template_context(ctx: PathContext, steps_dict: dict[str, Any]) -> dict[str, Any]:
    """Return the variables visible to step templates."""
    return {"inputs": ctx.inputs, "steps": steps_dict}


async def execute_step(ctx: PathContext, step: StepDefinition, steps_dict: dict[str, Any]) -> Any:
    """
    Execute one declared step and return its output.

    Raises
    ------
    PathError
        On template, model, JSON, tool, or transform failure.
    """
    context_dict = template_context(ctx, steps_dict)
    try:
        if step.type == "llm":
            messages = []
            if step.system_prompt:
                messages.append(
                    {"role": "system", "content": render_string(step.system_prompt, context_dict)}
                )
            messages.append(
                {"role": "user", "content": render_string(step.user_prompt or "", context_dict)}
            )
        else:
            args_dict = render_value(step.args, context_dict)
    except TemplateRenderError as exc:
        raise PathError("template_error", f"{step.step_id}: {exc}") from exc

    if step.type == "llm":
        result = await llm_step(
            ctx,
            step.step_id,
            step.step_class or "",
            messages,
            step.max_tokens,
            o_json=step.output == "json",
        )
        if step.output == "text":
            if not (result.content or "").strip():
                raise PathError("empty_output", f"{step.step_id} returned no text")
            return result.content.strip()
        try:
            return parse_json_output(result.content)
        except ValueError as exc:
            raise PathError("invalid_json", f"{step.step_id}: {exc}") from exc
    if step.type == "tool":
        return await tool_step(ctx, step.step_id, step.tool or "", args_dict)
    started = time.monotonic()
    try:
        output = run_transform(step.transform or "", args_dict)
    except (KeyError, TypeError, ValueError) as exc:
        ctx.traces.append(
            StepTrace(
                step.step_id,
                "transform",
                step.transform or "",
                STATUS_FAILED,
                0.0,
                {"error": str(exc)},
            )
        )
        raise PathError("transform_error", f"{step.step_id}: {exc}") from exc
    ctx.traces.append(
        StepTrace(
            step.step_id,
            "transform",
            step.transform or "",
            STATUS_COMPLETED,
            time.monotonic() - started,
        )
    )
    return output


# ============= Path =============


class PipelinePath:
    """Runs each declared step as a LangGraph node in sequence; the first failure stops the run."""

    name = PATH_NAME

    def build_graph(self, ctx: PathContext):
        """Compile a linear graph with one node per declared step."""
        graph = StateGraph(PipelineState)
        previous = START
        for step in ctx.workflow.steps:

            async def node(state: PipelineState, step: StepDefinition = step) -> dict[str, Any]:
                if state["error"] is not None:
                    return {}
                try:
                    output = await execute_step(ctx, step, state["steps"])
                except PathError as exc:
                    return {"error": exc}
                return {"steps": {**state["steps"], step.step_id: {"output": output}}}

            graph.add_node(step.step_id, node)
            graph.add_edge(previous, step.step_id)
            previous = step.step_id
        graph.add_edge(previous, END)
        return graph.compile()

    async def run(self, ctx: PathContext) -> PathResult:
        """Execute the workflow's steps."""
        final_state = await self.build_graph(ctx).ainvoke({"steps": {}, "error": None})
        return finish(ctx, final_state["error"])
