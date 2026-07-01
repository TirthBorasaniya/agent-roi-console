"""LangChain tool definitions for Slack and Notion operations."""

# ============= Standard Library =============
import json

# ============= Third-Party =============
from langchain_core.tools import tool

# ============= Local =============
from connectors.slack_connector import post_slack_message as _post_slack, read_slack_channel as _read_slack
from connectors.notion_connector import (
    read_notion_page as _read_notion_page,
    create_notion_page as _create_notion_page,
    search_notion as _search_notion,
)


# ============= Slack Tools =============

@tool
def post_slack_message(channel_id: str, text: str) -> str:
    """Post a message to a Slack channel and return the message timestamp."""
    return _post_slack(channel_id=channel_id, text=text)


@tool
def read_slack_channel(channel_id: str, limit: int = 10) -> str:
    """Read the most recent messages from a Slack channel. Returns JSON list of message dicts."""
    messages_list = _read_slack(channel_id=channel_id, limit=limit)
    return json.dumps(messages_list)


# ============= Notion Tools =============

@tool
def read_notion_page(page_id: str) -> str:
    """Read the text content of a Notion page."""
    return _read_notion_page(page_id=page_id)


@tool
def create_notion_page(parent_id: str, title: str, content: str) -> str:
    """Create a new Notion page and return its URL."""
    return _create_notion_page(parent_id=parent_id, title=title, content=content)


@tool
def search_notion(query: str) -> str:
    """Search Notion workspace and return matching page titles and URLs as JSON."""
    results_list = _search_notion(query=query)
    return json.dumps(results_list)


# ============= Tool Registry =============

ALL_TOOLS = [
    post_slack_message,
    read_slack_channel,
    read_notion_page,
    create_notion_page,
    search_notion,
]
