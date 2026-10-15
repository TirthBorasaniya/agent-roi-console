"""ReAct path: one agent loop that chooses MCP tool calls until it decides it is done."""

# ============= Standard Library =============
import json
from typing import Any, TypedDict

# ============= Third-Party =============
from langgraph.graph import END, START, StateGraph

# ============= Local =============
from roiconsole.paths.base import (
    PathContext,
    PathError,
    PathResult,
    finish,
    llm_step,
    tool_step,
    truncate_json,
)

# ============= Constants =============
PATH_NAME = "react"
AGENT_MAX_TOKENS = 1000
REACT_SYSTEM = (
    "You are an operations agent with Slack, Notion, and CRM tools. Complete the task by "
    "calling tools. Call each write tool once with complete content; do not post drafts. "
    "When the task is fully done, reply with a one-sentence summary and no tool calls."
)


# ============= State =============


class ReactState(TypedDict):
    """Graph state: the conversation, the turn counter, and the first error."""

    messages: list[dict[str, Any]]
    turn: int
    error: PathError | None
    o_done: bool


# ============= Path =============


class ReactPath:
    """The original console's tool loop, now over MCP tools and the gateway."""

    name = PATH_NAME

    def build_graph(self, ctx: PathContext, tool_list: list[dict[str, Any]]):
        """Compile agent <-> tools until the agent stops calling tools or the turn limit hits."""
        max_turns = ctx.workflow.agent.max_iterations

        async def agent_node(state: ReactState) -> dict[str, Any]:
            turn = state["turn"] + 1
            if turn > max_turns:
                return {"error": PathError("max_iterations", f"no finish within {max_turns} turns")}
            try:
                result = await llm_step(
                    ctx,
                    f"react_turn_{turn}",
                    ctx.workflow.agent.react_step_class,
                    state["messages"],
                    AGENT_MAX_TOKENS,
                    tools=tool_list,
                )
            except PathError as exc:
                return {"error": exc, "turn": turn}
            message: dict[str, Any] = {"role": "assistant", "content": result.content}
            if result.tool_calls:
                message["tool_calls"] = result.tool_calls
            return {
                "messages": [*state["messages"], message],
                "turn": turn,
                "o_done": not result.tool_calls,
            }

        async def tools_node(state: ReactState) -> dict[str, Any]:
            assistant = state["messages"][-1]
            tool_message_list = []
            for index, call in enumerate(assistant.get("tool_calls", [])):
                function = call.get("function", {})
                name = function.get("name", "")
                step_id = f"react_turn_{state['turn']}_call_{index}"
                try:
                    args_dict = json.loads(function.get("arguments") or "{}")
                    if not isinstance(args_dict, dict):
                        raise ValueError("arguments must be an object")
                    output = await tool_step(ctx, step_id, name, args_dict)
                    content = truncate_json(output)
                except (ValueError, json.JSONDecodeError) as exc:
                    content = json.dumps({"error": f"invalid arguments: {exc}"})
                except PathError as exc:
                    # tool errors go back to the agent, which may correct itself
                    content = json.dumps({"error": str(exc)[:1000]})
                tool_message_list.append(
                    {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}
                )
            return {"messages": [*state["messages"], *tool_message_list]}

        def after_agent(state: ReactState) -> str:
            if state["error"] is not None or state["o_done"]:
                return END
            return "tools"

        graph = StateGraph(ReactState)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", tools_node)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", after_agent, {"tools": "tools", END: END})
        graph.add_edge("tools", "agent")
        return graph.compile()

    async def run(self, ctx: PathContext) -> PathResult:
        """Run the agent loop."""
        tool_list = await ctx.toolbox.openai_tools(ctx.workflow.tool_list)
        initial = {
            "messages": [
                {"role": "system", "content": REACT_SYSTEM},
                {"role": "user", "content": ctx.instruction},
            ],
            "turn": 0,
            "error": None,
            "o_done": False,
        }
        recursion_limit = ctx.workflow.agent.max_iterations * 2 + 5
        final_state = await self.build_graph(ctx, tool_list).ainvoke(
            initial, {"recursion_limit": recursion_limit}
        )
        return finish(ctx, final_state["error"])
