"""Slack tool node for the LangGraph orchestrator."""

# ============= Standard Library =============
import logging

# ============= Third-Party =============
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode

# ============= Local =============
from agents.tools import post_slack_message, read_slack_channel

# ============= Constants =============
logger = logging.getLogger(__name__)

SLACK_TOOLS = [post_slack_message, read_slack_channel]

# ============= Node =============
# langgraph ToolNode handles invocation and error wrapping automatically
slack_tool_node = ToolNode(tools=SLACK_TOOLS)
