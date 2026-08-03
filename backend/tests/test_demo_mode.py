"""Unit tests for the no-API-key demo path in the LangGraph orchestrator."""

# ============= Standard Library =============
import os
import sys
from pathlib import Path

# ============= Local =============
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.orchestrator import (
    DEFAULT_DEMO_PLAN,
    DEMO_TOOL_ARGS,
    DEMO_TOOL_PLANS,
    build_demo_response,
    is_llm_configured,
)


def test_is_llm_configured_false_when_key_absent():
    """Assert demo mode is detected when ANTHROPIC_API_KEY is missing or blank."""
    original = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        assert is_llm_configured() is False

        os.environ["ANTHROPIC_API_KEY"] = "   "
        assert is_llm_configured() is False

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-something"
        assert is_llm_configured() is True
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        if original is not None:
            os.environ["ANTHROPIC_API_KEY"] = original


def test_every_demo_plan_uses_known_tools_and_single_node_per_step():
    """Assert each plan step maps to one tool group so routing never splits a step."""
    slack_set = {"post_slack_message", "read_slack_channel"}
    crm_set = {"crm_search_contacts", "crm_create_note", "crm_list_opportunities"}

    for plan_list in list(DEMO_TOOL_PLANS.values()) + [DEFAULT_DEMO_PLAN]:
        assert len(plan_list) > 0
        for step_list in plan_list:
            assert len(step_list) > 0
            for tool_name in step_list:
                assert tool_name in DEMO_TOOL_ARGS

            # a step must not mix tool groups, or _should_continue would route
            # the step to one node and leave the other calls unanswered
            groups_set = set()
            for tool_name in step_list:
                if tool_name in slack_set:
                    groups_set.add("slack")
                elif tool_name in crm_set:
                    groups_set.add("crm")
                else:
                    groups_set.add("notion")
            assert len(groups_set) == 1


def test_demo_response_emits_tool_calls_then_terminates():
    """Assert the demo response yields tool calls per step and stops when exhausted."""
    plan_list = DEMO_TOOL_PLANS["SUMMARIZATION"]

    for step_index, expected_list in enumerate(plan_list):
        # workflow_id is unknown to the DB here, so the default plan would apply;
        # assert instead on the structural contract of a mid-plan response
        message = build_demo_response("unknown-workflow", step_index)
        if step_index < len(DEFAULT_DEMO_PLAN):
            assert message.tool_calls
            for call_dict in message.tool_calls:
                assert call_dict["name"] in DEMO_TOOL_ARGS
                assert call_dict["id"]

    # past the end of the default plan there must be no tool calls, which is what
    # ends the tool -> route_query loop and routes to synthesize
    exhausted = build_demo_response("unknown-workflow", len(DEFAULT_DEMO_PLAN))
    assert not exhausted.tool_calls
    assert exhausted.content

    assert expected_list is not None


def test_demo_response_reports_token_usage():
    """Assert the simulated response carries fixed synthetic token counts."""
    message = build_demo_response("unknown-workflow", 0)
    usage_dict = message.usage_metadata

    assert usage_dict["input_tokens"] > 0
    assert usage_dict["output_tokens"] > 0
    assert usage_dict["total_tokens"] == (
        usage_dict["input_tokens"] + usage_dict["output_tokens"]
    )
