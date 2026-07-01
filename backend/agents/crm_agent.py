"""Twenty CRM tool node for the LangGraph orchestrator."""

# ============= Standard Library =============
import logging

# ============= Third-Party =============
from langgraph.prebuilt import ToolNode

# ============= Local =============
from agents.tools import crm_search_contacts, crm_create_note, crm_list_opportunities

# ============= Constants =============
logger = logging.getLogger(__name__)

CRM_TOOLS = [crm_search_contacts, crm_create_note, crm_list_opportunities]

# ============= Node =============
crm_tool_node = ToolNode(tools=CRM_TOOLS)
