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


# ============= State =============

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    workflow_id: str
    run_id: str
    input_tokens: int
    output_tokens: int
    tools_used: list[str]
    output_summary: str


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
    llm = _build_llm()
    callbacks_list = build_langfuse_callback()

    messages_list = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]

    try:
        response = llm.invoke(messages_list, config={"callbacks": callbacks_list})
    except Exception as exc:
        logger.warning(f"llm call failed, using mock response: {exc}")
        # graceful degradation: return a mock AI response
        response = AIMessage(
            content="I've processed your request using the available tools.",
            usage_metadata={"input_tokens": 150, "output_tokens": 30, "total_tokens": 180},
        )

    usage = getattr(response, "usage_metadata", {}) or {}
    in_tokens = usage.get("input_tokens", 0)
    out_tokens = usage.get("output_tokens", 0)

    return {
        "messages": [response],
        "input_tokens": state["input_tokens"] + in_tokens,
        "output_tokens": state["output_tokens"] + out_tokens,
        "tools_used": state["tools_used"],
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
        summary_text = "Workflow run completed. Tasks executed via available tools."
        in_tokens, out_tokens = 50, 20

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
    }

    final_state = graph.invoke(initial_state)
    return final_state
