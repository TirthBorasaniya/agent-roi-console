"""Notion tool node for the LangGraph orchestrator."""

# ============= Standard Library =============
import logging

# ============= Third-Party =============
from langgraph.prebuilt import ToolNode

# ============= Local =============
from agents.tools import read_notion_page, create_notion_page, search_notion

# ============= Constants =============
logger = logging.getLogger(__name__)

NOTION_TOOLS = [read_notion_page, create_notion_page, search_notion]

# ============= Node =============
notion_tool_node = ToolNode(tools=NOTION_TOOLS)
