"""LangGraph StateGraph orchestrator for multi-agent workflow execution."""

# ============= Standard Library =============
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Annotated, TypedDict

# ============= Third-Party =============
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# ============= Local =============
from agents.tools import ALL_TOOLS
from database import get_connection
from services.langfuse_client import build_langfuse_callback
from services.roi_calculator import compute_net_roi

# ============= Constants =============
logger = logging.getLogger(__name__)

DEFAULT_LOADED_COST = float(os.getenv("LOADED_COST_PER_HOUR", "75.0"))
DEFAULT_INPUT_PRICE = float(os.getenv("LLM_INPUT_PRICE_PER_1M", "3.0"))
DEFAULT_OUTPUT_PRICE = float(os.getenv("LLM_OUTPUT_PRICE_PER_1M", "15.0"))

SYSTEM_PROMPT = (
    "You are an automation assistant. "
    "Use the available Slack, Notion, and Twenty CRM tools to fulfill the user's request. "
    "Be concise and execute the task directly without unnecessary commentary. "
    "When done, provide a brief summary of what you accomplished."
)

# token counts attributed to each simulated LLM call when no API key is present.
# these are fixed constants, not measured usage: demo-mode cost and ROI figures
# are therefore simulated, and the README says so
DEMO_ROUTE_INPUT_TOKENS = 150
DEMO_ROUTE_OUTPUT_TOKENS = 30
DEMO_SYNTH_INPUT_TOKENS = 50
DEMO_SYNTH_OUTPUT_TOKENS = 20

# demo-mode tool plan, keyed by the workflow's seeded value_category so the
# simulated run exercises the connectors that workflow would really use.
# each entry is a list of steps; each step is one group of tool calls routed to a
# single tool node, so _should_continue never has to split a step across nodes
DEMO_TOOL_PLANS: dict[str, list[list[str]]] = {
    "SUMMARIZATION": [["read_slack_channel"], ["post_slack_message"]],
    "RESEARCH": [["search_notion"]],
    "DATA_ENTRY": [["create_notion_page"]],
    "COORDINATION": [["crm_list_opportunities"], ["post_slack_message"]],
    "COMMUNICATION": [["post_slack_message"]],
}
DEFAULT_DEMO_PLAN: list[list[str]] = [["read_slack_channel"]]

# placeholder arguments for simulated tool calls; the connectors return mock data
# when their own credentials are absent, so these values are never sent anywhere
DEMO_TOOL_ARGS: dict[str, dict] = {
    "read_slack_channel": {"channel_id": "C_DEMO_CHANNEL", "limit": 10},
    "post_slack_message": {"channel_id": "C_DEMO_CHANNEL", "text": "Automated summary posted by Agent ROI Console."},
    "search_notion": {"query": "onboarding roadmap"},
    "create_notion_page": {
        "parent_id": "demo-parent-page",
        "title": "Meeting Notes",
        "content": "Action items and key decisions captured automatically.",
    },
    "read_notion_page": {"page_id": "demo-page"},
    "crm_list_opportunities": {},
    "crm_search_contacts": {"query": "acme"},
}


# ============= State =============

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    workflow_id: str
    run_id: str
    input_tokens: int
    output_tokens: int
    tools_used: list[str]
    output_summary: str
    demo_step: int


# ============= Demo Mode =============

def is_llm_configured() -> bool:
    """
    Report whether a usable Anthropic API key is present.

    Returns
    -------
    bool
        True when ANTHROPIC_API_KEY is set to a non-empty value.
    """
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def resolve_demo_plan(workflow_id: str) -> list[list[str]]:
    """
    Look up the demo tool plan for a workflow from its seeded definition.

    The plan is keyed on the workflow's value_category rather than hardcoded to a
    single tool, so each seeded workflow exercises the connectors it actually
    describes and /api/metrics/cost-by-tool reports several distinct tools.

    Parameters
    ----------
    workflow_id : str
        ID of the workflow being run.

    Returns
    -------
    list[list[str]]
        Ordered steps, each a list of tool names routed to one tool node.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value_category FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
    except sqlite3.Error as exc:
        logger.warning(f"could not resolve demo plan for {workflow_id}: {exc}")
        return DEFAULT_DEMO_PLAN
    finally:
        conn.close()

    if row is None:
        return DEFAULT_DEMO_PLAN
    return DEMO_TOOL_PLANS.get(row["value_category"], DEFAULT_DEMO_PLAN)


def build_demo_response(workflow_id: str, demo_step: int) -> AIMessage:
    """
    Build the simulated assistant turn used when no LLM key is configured.

    Emits the tool calls for the current step of the workflow's demo plan so the
    graph routes through the real tool nodes and per-tool usage is recorded. Once
    the plan is exhausted it returns a plain message with no tool calls, which
    ends the tool loop and routes to synthesize.

    Parameters
    ----------
    workflow_id : str
        ID of the workflow being run.
    demo_step : int
        Number of demo plan steps already executed.

    Returns
    -------
    AIMessage
        Simulated response, with tool_calls only while steps remain.
    """
    plan_list = resolve_demo_plan(workflow_id)
    usage_dict = {
        "input_tokens": DEMO_ROUTE_INPUT_TOKENS,
        "output_tokens": DEMO_ROUTE_OUTPUT_TOKENS,
        "total_tokens": DEMO_ROUTE_INPUT_TOKENS + DEMO_ROUTE_OUTPUT_TOKENS,
    }

    if demo_step >= len(plan_list):
        return AIMessage(
            content="I've processed your request using the available tools.",
            usage_metadata=usage_dict,
        )

    tool_calls_list = [
        {
            "name": tool_name,
            "args": DEMO_TOOL_ARGS.get(tool_name, {}),
            "id": f"demo-{uuid.uuid4()}",
        }
        for tool_name in plan_list[demo_step]
    ]
    return AIMessage(content="", tool_calls=tool_calls_list, usage_metadata=usage_dict)


# ============= LLM =============

def _build_llm() -> ChatAnthropic:
    """
    Build the ChatAnthropic LLM with tool binding.

    Returns
    -------
    ChatAnthropic
        LLM instance bound to all available tools.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "demo-key")
    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=2048,
    )
    return llm.bind_tools(ALL_TOOLS)


# ============= Graph Nodes =============

def route_query_node(state: AgentState) -> AgentState:
    """
    Invoke the LLM to classify intent and select tools. Accumulates token counts.

    Parameters
    ----------
    state : AgentState
        Current graph state.

    Returns
    -------
    AgentState
        Updated state with the LLM response appended and token counts incremented.
    """
    demo_step = state.get("demo_step", 0)

    if not is_llm_configured():
        # skip the round-trip entirely rather than waiting on a guaranteed 401
        response = build_demo_response(state["workflow_id"], demo_step)
        demo_step += 1
    else:
        llm = _build_llm()
        callbacks_list = build_langfuse_callback()
        messages_list = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        try:
            response = llm.invoke(messages_list, config={"callbacks": callbacks_list})
        except Exception as exc:
            logger.warning(f"llm call failed, using simulated response: {exc}")
            # graceful degradation: fall back to the same demo path
            response = build_demo_response(state["workflow_id"], demo_step)
            demo_step += 1

    usage = getattr(response, "usage_metadata", {}) or {}
    in_tokens = usage.get("input_tokens", 0)
    out_tokens = usage.get("output_tokens", 0)

    return {
        "messages": [response],
        "input_tokens": state["input_tokens"] + in_tokens,
        "output_tokens": state["output_tokens"] + out_tokens,
        "tools_used": state["tools_used"],
        "demo_step": demo_step,
    }


def _should_continue(state: AgentState) -> str:
    """
    Route to tool execution or synthesize based on the last message.

    Parameters
    ----------
    state : AgentState
        Current graph state.

    Returns
    -------
    str
        "tools" if tool calls are present, else "synthesize".
    """
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None)
    if tool_calls:
        # detect which category of tool was called
        tool_names_set = {tc["name"] for tc in tool_calls}
        if tool_names_set & {"post_slack_message", "read_slack_channel"}:
            return "slack"
        if tool_names_set & {"crm_search_contacts", "crm_create_note", "crm_list_opportunities"}:
            return "crm"
        return "notion"
    return "synthesize"


def _tool_node_wrapper(tool_node: ToolNode, tool_label: str):
    """
    Wrap a ToolNode to track which tools were used in state.

    Parameters
    ----------
    tool_node : ToolNode
        The prebuilt tool node to wrap.
    tool_label : str
        Label used to identify this tool group.

    Returns
    -------
    callable
        Node function that updates tools_used and delegates to the tool node.
    """
    def _inner(state: AgentState) -> AgentState:
        last_msg = state["messages"][-1]
        tool_calls = getattr(last_msg, "tool_calls", [])
        used_names_list = [tc["name"] for tc in tool_calls]

        result = tool_node.invoke(state)
        updated_tools_list = list(state["tools_used"]) + used_names_list
        result["tools_used"] = updated_tools_list
        return result

    return _inner


def _build_demo_summary(tools_used_list: list[str]) -> str:
    """
    Compose the simulated run summary, naming the tools that actually executed.

    Parameters
    ----------
    tools_used_list : list[str]
        Tool names invoked during this run.

    Returns
    -------
    str
        Human-readable summary for the run record.
    """
    if not tools_used_list:
        return "Workflow run completed in demo mode. No tools were invoked."
    tool_names_str = ", ".join(sorted(set(tools_used_list)))
    return (
        f"Demo run completed. Simulated {len(tools_used_list)} tool call(s) "
        f"via mocked connectors: {tool_names_str}."
    )


def synthesize_node(state: AgentState) -> AgentState:
    """
    Summarize the run outcome, compute ROI, and write the RunRecord to the DB.

    Parameters
    ----------
    state : AgentState
        Current graph state after all tool calls complete.

    Returns
    -------
    AgentState
        Final state with output_summary populated.
    """
    tools_used_list = state["tools_used"]

    if not is_llm_configured():
        summary_text = _build_demo_summary(tools_used_list)
        in_tokens, out_tokens = DEMO_SYNTH_INPUT_TOKENS, DEMO_SYNTH_OUTPUT_TOKENS
    else:
        llm_raw = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            api_key=os.getenv("ANTHROPIC_API_KEY", "demo-key"),
            max_tokens=512,
        )
        callbacks_list = build_langfuse_callback()

        synthesis_prompt_list = [
            SystemMessage(content="Summarize in 1-2 sentences what was accomplished in this workflow run."),
        ] + state["messages"]

        try:
            summary_response = llm_raw.invoke(synthesis_prompt_list, config={"callbacks": callbacks_list})
            summary_text = summary_response.content
            usage = getattr(summary_response, "usage_metadata", {}) or {}
            in_tokens = usage.get("input_tokens", 0)
            out_tokens = usage.get("output_tokens", 0)
        except Exception as exc:
            logger.warning(f"synthesis llm call failed: {exc}")
            summary_text = _build_demo_summary(tools_used_list)
            in_tokens, out_tokens = DEMO_SYNTH_INPUT_TOKENS, DEMO_SYNTH_OUTPUT_TOKENS

    total_input = state["input_tokens"] + in_tokens
    total_output = state["output_tokens"] + out_tokens

    _write_run_record(
        run_id=state["run_id"],
        workflow_id=state["workflow_id"],
        input_tokens=total_input,
        output_tokens=total_output,
        tools_used_list=state["tools_used"],
        output_summary=summary_text,
    )

    return {
        "output_summary": summary_text,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }


def _write_run_record(
    run_id: str,
    workflow_id: str,
    input_tokens: int,
    output_tokens: int,
    tools_used_list: list[str],
    output_summary: str,
) -> None:
    """
    Update the run record in the database with final token counts and ROI.

    Parameters
    ----------
    run_id : str
        The run's primary key.
    workflow_id : str
        The parent workflow ID.
    input_tokens : int
        Accumulated input tokens.
    output_tokens : int
        Accumulated output tokens.
    tools_used_list : list[str]
        Names of tools invoked during this run.
    output_summary : str
        LLM-generated summary of the run outcome.

    Returns
    -------
    None
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT baseline_minutes FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        baseline_minutes = row["baseline_minutes"] if row else 10.0

        net_roi_usd, token_cost_usd = compute_net_roi(
            baseline_minutes=baseline_minutes,
            loaded_cost_per_hour=DEFAULT_LOADED_COST,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price_per_1m=DEFAULT_INPUT_PRICE,
            output_price_per_1m=DEFAULT_OUTPUT_PRICE,
        )

        completed_at = datetime.now(timezone.utc).isoformat()
        tools_used_str = json.dumps(list(set(tools_used_list)))

        conn.execute(
            """UPDATE runs SET
                status = 'COMPLETED',
                output_summary = ?,
                input_tokens = ?,
                output_tokens = ?,
                token_cost_usd = ?,
                net_roi_usd = ?,
                tools_used = ?,
                completed_at = ?
            WHERE id = ?""",
            (
                output_summary,
                input_tokens,
                output_tokens,
                round(token_cost_usd, 6),
                round(net_roi_usd, 6),
                tools_used_str,
                completed_at,
                run_id,
            ),
        )

        # record per-tool usage aggregated by tool name
        for tool_name in set(tools_used_list):
            call_count = tools_used_list.count(tool_name)
            per_tool_input = input_tokens // max(len(set(tools_used_list)), 1)
            per_tool_output = output_tokens // max(len(set(tools_used_list)), 1)
            per_tool_cost = token_cost_usd / max(len(set(tools_used_list)), 1)
            conn.execute(
                """INSERT INTO tool_usage
                    (id, run_id, tool_name, input_tokens, output_tokens, cost_usd, latency_ms, called_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    run_id,
                    tool_name,
                    per_tool_input * call_count,
                    per_tool_output * call_count,
                    round(per_tool_cost * call_count, 6),
                    0,
                    completed_at,
                ),
            )

        conn.commit()
    finally:
        conn.close()


# ============= Graph Builder =============

def build_graph() -> StateGraph:
    """
    Construct and compile the LangGraph StateGraph.

    Returns
    -------
    StateGraph
        Compiled runnable graph.
    """
    from agents.slack_agent import slack_tool_node
    from agents.notion_agent import notion_tool_node
    from agents.crm_agent import crm_tool_node

    slack_node = _tool_node_wrapper(slack_tool_node, "slack")
    notion_node = _tool_node_wrapper(notion_tool_node, "notion")
    crm_node = _tool_node_wrapper(crm_tool_node, "crm")

    graph = StateGraph(AgentState)
    graph.add_node("route_query", route_query_node)
    graph.add_node("slack", slack_node)
    graph.add_node("notion", notion_node)
    graph.add_node("crm", crm_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_entry_point("route_query")

    graph.add_conditional_edges(
        "route_query",
        _should_continue,
        {"slack": "slack", "notion": "notion", "crm": "crm", "synthesize": "synthesize"},
    )
    # after tool execution, loop back so the LLM can decide if more tools are needed
    graph.add_edge("slack", "route_query")
    graph.add_edge("notion", "route_query")
    graph.add_edge("crm", "route_query")
    graph.add_edge("synthesize", END)

    return graph.compile()


# ============= Public Invocation =============

def run_workflow(workflow_id: str, run_id: str, user_input: str) -> dict:
    """
    Execute the full LangGraph workflow and return the final state.

    Parameters
    ----------
    workflow_id : str
        ID of the workflow being run.
    run_id : str
        Pre-created run record ID.
    user_input : str
        Natural language instruction from the user or default task description.

    Returns
    -------
    dict
        Final AgentState after graph execution.
    """
    graph = build_graph()

    initial_state: AgentState = {
        "messages": [HumanMessage(content=user_input)],
        "workflow_id": workflow_id,
        "run_id": run_id,
        "input_tokens": 0,
        "output_tokens": 0,
        "tools_used": [],
        "output_summary": "",
        "demo_step": 0,
    }

    final_state = graph.invoke(initial_state)
    return final_state
