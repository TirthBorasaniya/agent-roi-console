"""Notion API wrapper with graceful degradation when credentials are absent."""

# ============= Standard Library =============
import logging
import os

# ============= Third-Party =============
try:
    from notion_client import Client as NotionClient
    from notion_client.errors import APIResponseError
    _NOTION_AVAILABLE = True
except ImportError:
    _NOTION_AVAILABLE = False

# ============= Constants =============
logger = logging.getLogger(__name__)

MOCK_PAGES = [
    {"title": "Q4 Roadmap", "url": "https://notion.so/mock-q4-roadmap", "id": "mock-page-001"},
    {"title": "Architecture Decision Records", "url": "https://notion.so/mock-adr", "id": "mock-page-002"},
    {"title": "Onboarding Guide", "url": "https://notion.so/mock-onboarding", "id": "mock-page-003"},
]

MOCK_PAGE_CONTENT = (
    "# Mock Notion Page\n\n"
    "This is placeholder content returned when the Notion API key is not configured.\n\n"
    "## Key Points\n"
    "- Agent ROI Console runs in demo mode without real API credentials\n"
    "- Configure NOTION_API_KEY in .env to connect to a real workspace\n"
    "- All data shown on the dashboard is computed from actual run records\n"
)


# ============= Client Factory =============

def _get_client() -> "NotionClient | None":
    """
    Return a Notion client if credentials are configured, else None.

    Returns
    -------
    NotionClient | None
        Authenticated client or None when the API key is absent.
    """
    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        logger.warning("NOTION_API_KEY not set, running in mock mode")
        return None
    if not _NOTION_AVAILABLE:
        logger.warning("notion_client not installed, running in mock mode")
        return None
    return NotionClient(auth=api_key)


# ============= Public Functions =============

def read_notion_page(page_id: str) -> str:
    """
    Read the text content of a Notion page.

    Parameters
    ----------
    page_id : str
        The Notion page ID to read.

    Returns
    -------
    str
        Markdown-like text content of the page.
    """
    client = _get_client()
    if client is None:
        logger.info(f"mock: reading notion page {page_id}")
        return MOCK_PAGE_CONTENT

    try:
        blocks = client.blocks.children.list(block_id=page_id)
        text_parts_list = []
        for block in blocks.get("results", []):
            block_type = block.get("type", "")
            block_data = block.get(block_type, {})
            rich_text_list = block_data.get("rich_text", [])
            for segment in rich_text_list:
                text_parts_list.append(segment.get("plain_text", ""))
        return "\n".join(text_parts_list) or MOCK_PAGE_CONTENT
    except APIResponseError as exc:
        logger.error(f"notion read failed: {exc}")
        return MOCK_PAGE_CONTENT


def create_notion_page(parent_id: str, title: str, content: str) -> str:
    """
    Create a new Notion page and return its URL.

    Parameters
    ----------
    parent_id : str
        The parent page or database ID in Notion.
    title : str
        The title of the new page.
    content : str
        The body text for the new page.

    Returns
    -------
    str
        URL of the newly created Notion page.
    """
    client = _get_client()
    if client is None:
        mock_url = f"https://notion.so/mock-{title.lower().replace(' ', '-')}"
        logger.info(f"mock: would create notion page '{title}' under {parent_id}")
        return mock_url

    try:
        paragraphs_list = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": line}}]
                },
            }
            for line in content.split("\n")
            if line.strip()
        ]
        response = client.pages.create(
            parent={"page_id": parent_id},
            properties={"title": [{"type": "text", "text": {"content": title}}]},
            children=paragraphs_list,
        )
        return response.get("url", "https://notion.so/mock")
    except APIResponseError as exc:
        logger.error(f"notion create failed: {exc}")
        return f"https://notion.so/mock-error"


def search_notion(query: str) -> list[dict]:
    """
    Search Notion workspace and return matching page titles and URLs.

    Parameters
    ----------
    query : str
        The search query string.

    Returns
    -------
    list[dict]
        List of dicts with keys: title, url, id.
    """
    client = _get_client()
    if client is None:
        logger.info(f"mock: searching notion for '{query}'")
        return [p for p in MOCK_PAGES if query.lower() in p["title"].lower()] or MOCK_PAGES

    try:
        response = client.search(query=query, filter={"property": "object", "value": "page"})
        results_list = []
        for page in response.get("results", []):
            title_list = (
                page.get("properties", {})
                .get("title", {})
                .get("title", [])
            )
            title_text = title_list[0]["plain_text"] if title_list else "Untitled"
            results_list.append({
                "title": title_text,
                "url": page.get("url", ""),
                "id": page.get("id", ""),
            })
        return results_list
    except APIResponseError as exc:
        logger.error(f"notion search failed: {exc}")
        return MOCK_PAGES
