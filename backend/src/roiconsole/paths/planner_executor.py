"""Planner-executor path: one LLM call writes a JSON plan, an executor runs it step by step."""

# ============= Standard Library =============
import json
import re
from typing import Any, TypedDict

# ============= Third-Party =============
from langgraph.graph import END, START, StateGraph

# ============= Local =============
from roiconsole.paths.base import PathContext, PathError, PathResult, finish, llm_step, tool_step
from roiconsole.runner.llm import parse_json_output
from roiconsole.runner.mcp_client import ToolSpec

# ============= Constants =============
PATH_NAME = "planner_executor"
MAX_PLAN_STEPS = 12
PLANNER_MAX_TOKENS = 1200
GENERATE_MAX_TOKENS = 600
PLACEHOLDER_RE = re.compile(r"\{\{\s*(s\d+)((?:\.[A-Za-z0-9_]+)*)\s*\}\}")
PLANNER_SYSTEM = (
    "You plan tool-using workflows. Respond with a JSON object only: "
    '{"steps": [...]} where each step is either '
    '{"id": "s1", "action": "tool", "tool": "<server.tool>", "args": {...}} or '
    '{"id": "s2", "action": "generate", "instruction": "<what to write>", '
    '"inputs": ["s1"], "output": "text" | "json"}. '
    'Use "{{s1}}" or "{{s1.field}}" inside args to pass an earlier step\'s output; '
    "a value that is exactly one placeholder is passed as the original value, not text. "
    "Give tool arguments their literal values when they are known from the task. "
    f"Use at most {MAX_PLAN_STEPS} steps and only the listed tools."
)
EXECUTOR_SYSTEM = (
    "You carry out one step of a plan. Follow the instruction using only the provided "
    "inputs. Output only the requested result."
)


# ============= Plan Handling =============


def describe_tools(spec_list: list[ToolSpec]) -> str:
    """Render the tool catalog for the planner, without idempotency keys."""
    line_list = []
    for spec in spec_list:
        parameters = spec.openai_schema()["function"]["parameters"]
        line_list.append(
            f"- {spec.qualified_name}: {spec.description} "
            f"Parameters: {json.dumps(parameters.get('properties', {}))}"
        )
    return "\n".join(line_list)


def validate_plan(plan: Any, allowed_tool_list: list[str]) -> list[dict[str, Any]]:
    """
    Check a plan's structure.

    Returns
    -------
    steps : list of dict
        Validated steps.

    Raises
    ------
    PathError
        reason invalid_plan when the shape, ids, tools, or references are wrong.
    """
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        raise PathError("invalid_plan", "plan must be an object with a non-empty steps list")
    step_list = plan["steps"]
    if len(step_list) > MAX_PLAN_STEPS:
        raise PathError("invalid_plan", f"plan has {len(step_list)} steps, limit {MAX_PLAN_STEPS}")
    seen_set: set[str] = set()
    for step in step_list:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str):
            raise PathError("invalid_plan", f"step without id: {step!r}")
        if step["id"] in seen_set or not re.fullmatch(r"s\d+", step["id"]):
            raise PathError("invalid_plan", f"bad or duplicate step id {step['id']!r}")
        action = step.get("action")
        if action == "tool":
            if step.get("tool") not in allowed_tool_list:
                raise PathError("invalid_plan", f"tool {step.get('tool')!r} is not allowed")
            if not isinstance(step.get("args", {}), dict):
                raise PathError("invalid_plan", f"{step['id']}: args must be an object")
        elif action == "generate":
            if not isinstance(step.get("instruction"), str):
                raise PathError("invalid_plan", f"{step['id']}: generate needs an instruction")
            unknown_list = [i for i in step.get("inputs", []) if i not in seen_set]
            if unknown_list:
                raise PathError(
                    "invalid_plan",
                    f"{step['id']}: inputs reference later or unknown steps {unknown_list}",
                )
        else:
            raise PathError("invalid_plan", f"{step['id']}: unknown action {action!r}")
        referenced_set = set(PLACEHOLDER_RE.findall(json.dumps(step.get("args", {}))))
        missing_list = [ref for ref, _ in referenced_set if ref not in seen_set]
        if missing_list:
            raise PathError(
                "invalid_plan",
                f"{step['id']}: placeholders reference later or unknown steps {missing_list}",
            )
        seen_set.add(step["id"])
    return step_list


def _lookup(outputs_dict: dict[str, Any], ref: str, path: str) -> Any:
    """Follow `.a.b` attribute paths into an earlier step's output."""
    value = outputs_dict[ref]
    for part in [p for p in path.split(".") if p]:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise PathError("unresolved_placeholder", f"{ref}{path} not found in step output")
    return value


def resolve_placeholders(value: Any, outputs_dict: dict[str, Any]) -> Any:
    """Substitute {{sN.path}} placeholders: whole-string placeholders keep native values."""
    if isinstance(value, dict):
        return {k: resolve_placeholders(v, outputs_dict) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, outputs_dict) for v in value]
    if not isinstance(value, str):
        return value
    whole = PLACEHOLDER_RE.fullmatch(value.strip())
    if whole:
        return _lookup(outputs_dict, whole.group(1), whole.group(2))

    def substitute(match: re.Match) -> str:
        found = _lookup(outputs_dict, match.group(1), match.group(2))
        return found if isinstance(found, str) else json.dumps(found)

    return PLACEHOLDER_RE.sub(substitute, value)


def required_tool_order_ok(step_list: list[dict[str, Any]], required_list: list[str]) -> bool:
    """True when the plan's tool calls contain the required tools as an ordered subsequence."""
    tool_iter = iter([s["tool"] for s in step_list if s.get("action") == "tool"])
    return all(any(t == required for t in tool_iter) for required in required_list)


# ============= Path =============


class PlanState(TypedDict):
    """Graph state: the plan, outputs by plan step id, and the first error."""

    plan: list[dict[str, Any]]
    outputs: dict[str, Any]
    error: PathError | None


class PlannerExecutorPath:
    """Plan once with the planning step class, then execute tool calls and generation steps."""

    name = PATH_NAME

    async def plan(self, ctx: PathContext) -> list[dict[str, Any]]:
        """Ask the planner for a plan and validate it."""
        specs_dict = await ctx.toolbox.list_tools()
        spec_list = [specs_dict[name] for name in ctx.workflow.tool_list]
        user_prompt = (
            f"Task:\n{ctx.instruction}\n\nAvailable tools:\n{describe_tools(spec_list)}\n\n"
            "Write the plan."
        )
        result = await llm_step(
            ctx,
            "plan",
            ctx.workflow.agent.planner_step_class,
            [
                {"role": "system", "content": PLANNER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            PLANNER_MAX_TOKENS,
            o_json=True,
        )
        try:
            plan = parse_json_output(result.content)
        except ValueError as exc:
            raise PathError("invalid_plan", str(exc)) from exc
        return validate_plan(plan, ctx.workflow.tool_list)

    async def execute(
        self, ctx: PathContext, step: dict[str, Any], outputs_dict: dict[str, Any]
    ) -> Any:
        """Execute one plan step."""
        step_id = f"exec_{step['id']}"
        if step["action"] == "tool":
            args_dict = resolve_placeholders(step.get("args", {}), outputs_dict)
            return await tool_step(ctx, step_id, step["tool"], args_dict)
        input_text = "\n\n".join(
            f"Output of {ref}:\n{json.dumps(outputs_dict[ref], default=str)}"
            for ref in step.get("inputs", [])
        )
        o_json = step.get("output") == "json"
        user_prompt = f"Instruction: {step['instruction']}\n\n{input_text}".strip()
        if o_json:
            user_prompt += "\n\nRespond with a JSON object only."
        result = await llm_step(
            ctx,
            step_id,
            ctx.workflow.agent.executor_step_class,
            [
                {"role": "system", "content": EXECUTOR_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            GENERATE_MAX_TOKENS,
            o_json=o_json,
        )
        if not o_json:
            return (result.content or "").strip()
        try:
            return parse_json_output(result.content)
        except ValueError as exc:
            raise PathError("invalid_json", f"{step_id}: {exc}") from exc

    def build_graph(self, ctx: PathContext):
        """Compile plan -> execute loop -> end."""

        async def plan_node(state: PlanState) -> dict[str, Any]:
            try:
                return {"plan": await self.plan(ctx)}
            except PathError as exc:
                return {"error": exc}

        async def execute_node(state: PlanState) -> dict[str, Any]:
            step = state["plan"][len(state["outputs"])]
            try:
                output = await self.execute(ctx, step, state["outputs"])
            except PathError as exc:
                return {"error": exc}
            return {"outputs": {**state["outputs"], step["id"]: output}}

        def next_edge(state: PlanState) -> str:
            if state["error"] is not None or len(state["outputs"]) >= len(state["plan"]):
                return END
            return "execute"

        graph = StateGraph(PlanState)
        graph.add_node("plan", plan_node)
        graph.add_node("execute", execute_node)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", next_edge, {"execute": "execute", END: END})
        graph.add_conditional_edges("execute", next_edge, {"execute": "execute", END: END})
        return graph.compile()

    async def run(self, ctx: PathContext) -> PathResult:
        """Plan and execute."""
        final_state = await self.build_graph(ctx).ainvoke(
            {"plan": [], "outputs": {}, "error": None}, {"recursion_limit": MAX_PLAN_STEPS * 2 + 5}
        )
        return finish(ctx, final_state["error"])
