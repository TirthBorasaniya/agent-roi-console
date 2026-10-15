"""Notion backends: the live Notion API (2025-09-03, data sources) and the SQLite sandbox."""

# ============= Standard Library =============
import logging
import re
from datetime import UTC, datetime
from typing import Any, Protocol

# ============= Third-Party =============
from mcp.server.fastmcp.exceptions import ToolError
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError

# ============= Local =============
from roiconsole.mcp_servers.common import WriteThrottle
from roiconsole.sandbox.store import SandboxStore
from roiconsole.validators.state import NotionBlock, NotionPage

# ============= Constants =============
logger = logging.getLogger(__name__)

BLOCK_TYPES = ("heading_2", "paragraph", "bulleted_list_item", "to_do")
PROPERTY_TYPES = ("multi_select", "rich_text", "date")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_BLOCKS_PER_APPEND = 100
MAX_TEXT_LENGTH = 2000
PAGE_SIZE = 100


# ============= Conversion Helpers =============


def validate_properties(
    properties_dict: dict[str, Any], schema_dict: dict[str, str]
) -> dict[str, Any]:
    """
    Check simplified page properties against a database schema.

    Parameters
    ----------
    properties_dict : dict
        Simplified values: multi_select as a list of names, rich_text as a
        string, date as "YYYY-MM-DD".
    schema_dict : dict
        Property name to type.

    Returns
    -------
    clean_dict : dict
        Validated simplified properties.
    """
    clean_dict: dict[str, Any] = {}
    for name, value in (properties_dict or {}).items():
        if name not in schema_dict:
            raise ToolError(f"unknown property {name!r}; allowed: {sorted(schema_dict)}")
        kind = schema_dict[name]
        if kind == "multi_select":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ToolError(f"property {name} must be a list of strings")
            clean_dict[name] = [v.replace(",", " ").strip() for v in value if v.strip()]
        elif kind == "rich_text":
            clean_dict[name] = str(value)[:MAX_TEXT_LENGTH]
        elif kind == "date":
            if not isinstance(value, str) or not DATE_RE.match(value):
                raise ToolError(f"property {name} must be a date string YYYY-MM-DD")
            clean_dict[name] = value
    return clean_dict


def validate_blocks(block_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check simplified blocks: each has a supported type and text."""
    if not isinstance(block_list, list) or not block_list:
        raise ToolError("blocks must be a non-empty list")
    if len(block_list) > MAX_BLOCKS_PER_APPEND:
        raise ToolError(f"at most {MAX_BLOCKS_PER_APPEND} blocks per call")
    clean_list = []
    for block in block_list:
        if not isinstance(block, dict) or block.get("type") not in BLOCK_TYPES:
            raise ToolError(f"each block needs type in {BLOCK_TYPES}: {block!r}")
        clean = {"type": block["type"], "text": str(block.get("text", ""))[:MAX_TEXT_LENGTH]}
        if block["type"] == "to_do":
            clean["checked"] = bool(block.get("checked", False))
        clean_list.append(clean)
    return clean_list


def _rich_text(text: str) -> list[dict[str, Any]]:
    """Return a Notion rich_text array for plain text."""
    return [{"type": "text", "text": {"content": text}}]


def to_notion_properties(
    title_property: str, title: str, clean_dict: dict[str, Any], schema_dict: dict[str, str]
) -> dict[str, Any]:
    """Convert simplified properties to the Notion API page property format."""
    property_dict: dict[str, Any] = {title_property: {"title": _rich_text(title)}}
    for name, value in clean_dict.items():
        kind = schema_dict[name]
        if kind == "multi_select":
            property_dict[name] = {"multi_select": [{"name": v} for v in value]}
        elif kind == "rich_text":
            property_dict[name] = {"rich_text": _rich_text(value)}
        elif kind == "date":
            property_dict[name] = {"date": {"start": value}}
    return property_dict


def to_notion_block(block: dict[str, Any]) -> dict[str, Any]:
    """Convert a simplified block to a Notion API block object."""
    body: dict[str, Any] = {"rich_text": _rich_text(block["text"])}
    if block["type"] == "to_do":
        body["checked"] = block.get("checked", False)
    return {"object": "block", "type": block["type"], block["type"]: body}


def _plain_text(rich_text_list: list[dict[str, Any]]) -> str:
    """Join the plain text of a rich_text array."""
    return "".join(segment.get("plain_text", "") for segment in rich_text_list or [])


def from_notion_page(page: dict[str, Any], schema_dict: dict[str, str]) -> dict[str, Any]:
    """Convert a Notion API page to {id, title, properties, url} with simplified values."""
    title = ""
    simple_dict: dict[str, Any] = {}
    for name, prop in page.get("properties", {}).items():
        kind = prop.get("type")
        if kind == "title":
            title = _plain_text(prop.get("title", []))
        elif name in schema_dict and kind == "multi_select":
            simple_dict[name] = [o["name"] for o in prop.get("multi_select", [])]
        elif name in schema_dict and kind == "rich_text":
            simple_dict[name] = _plain_text(prop.get("rich_text", []))
        elif name in schema_dict and kind == "date":
            simple_dict[name] = (prop.get("date") or {}).get("start")
    return {"id": page["id"], "title": title, "properties": simple_dict, "url": page.get("url", "")}


# ============= Sandbox Filter =============


def page_matches_filter(page: dict[str, Any], filter_dict: dict[str, Any] | None) -> bool:
    """
    Evaluate the supported subset of the Notion filter language on a sandbox page.

    Supported: and, or, and property conditions on title, rich_text (contains,
    equals), multi_select (contains), and date (equals, on_or_after, on_or_before).
    """
    if not filter_dict:
        return True
    if "and" in filter_dict:
        return all(page_matches_filter(page, f) for f in filter_dict["and"])
    if "or" in filter_dict:
        return any(page_matches_filter(page, f) for f in filter_dict["or"])
    name = filter_dict.get("property")
    if name is None:
        raise ToolError(f"unsupported filter: {filter_dict!r}")
    value = page["title"] if "title" in filter_dict else page["properties"].get(name)
    for kind in ("title", "rich_text", "multi_select", "date"):
        if kind not in filter_dict:
            continue
        condition = filter_dict[kind]
        if kind == "multi_select":
            if "contains" not in condition:
                raise ToolError("multi_select filters support contains")
            wanted = str(condition["contains"]).casefold()
            return wanted in {str(v).casefold() for v in value or []}
        if kind == "date":
            start = value or ""
            if "equals" in condition:
                return start == condition["equals"]
            if "on_or_after" in condition:
                return bool(start) and start >= condition["on_or_after"]
            if "on_or_before" in condition:
                return bool(start) and start <= condition["on_or_before"]
            raise ToolError("date filters support equals, on_or_after, on_or_before")
        text = str(value or "").casefold()
        if "contains" in condition:
            return str(condition["contains"]).casefold() in text
        if "equals" in condition:
            return str(condition["equals"]).casefold() == text
        raise ToolError(f"{kind} filters support contains and equals")
    raise ToolError(f"unsupported filter: {filter_dict!r}")


# ============= Interface =============


class NotionBackend(Protocol):
    """Operations behind notion-mcp, plus readers for validators and resets."""

    def create_page(
        self, parent_id: str, title: str, properties: dict[str, Any], run_id: str
    ) -> dict[str, Any]:
        """Create a database page and return {page_id, url}."""
        ...

    def query_database(self, database_id: str, filter: dict[str, Any] | None) -> dict[str, Any]:
        """Return {database_id, pages: [{id, title, properties, url}]}."""
        ...

    def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        """Append blocks to a page and return {page_id, block_ids}."""
        ...

    def reset_database(self, name: str, page_list: list[dict[str, Any]]) -> dict[str, str]:
        """Remove all pages from a database, create fixture pages, return ref to page id."""
        ...

    def pages_since(self, since: float) -> list[NotionPage]:
        """Return pages created at or after `since`, with blocks."""
        ...


# ============= Sandbox Backend =============


class SandboxNotionBackend:
    """
    Notion backed by the SQLite sandbox store.

    Parameters
    ----------
    store : SandboxStore
        Shared sandbox state.
    database_names : list of str
        Allowlisted logical database names.
    """

    def __init__(self, store: SandboxStore, database_names: list[str]) -> None:
        self._store = store
        self._database_names = list(database_names)

    def _database(self, name_or_id: str) -> dict[str, Any]:
        """Resolve an allowlisted database by name or id."""
        database = self._store.database(name_or_id)
        if database is None or database["name"] not in self._database_names:
            raise ToolError(f"database {name_or_id!r} is not in the allowlist")
        return database

    def create_page(
        self, parent_id: str, title: str, properties: dict[str, Any], run_id: str
    ) -> dict[str, Any]:
        """Insert a page into an allowlisted sandbox database."""
        if not title.strip():
            raise ToolError("title must not be empty")
        database = self._database(parent_id)
        clean_dict = validate_properties(properties, database["schema"])
        page_id = self._store.add_page(database["id"], title.strip(), clean_dict, run_id=run_id)
        return {"page_id": page_id, "url": f"sandbox://notion/{page_id}"}

    def query_database(self, database_id: str, filter: dict[str, Any] | None) -> dict[str, Any]:
        """Filter sandbox pages."""
        database = self._database(database_id)
        page_list = [
            {
                "id": p["id"],
                "title": p["title"],
                "properties": p["properties"],
                "url": f"sandbox://notion/{p['id']}",
            }
            for p in self._store.database_pages(database["id"])
            if page_matches_filter(p, filter)
        ]
        return {"database_id": database_id, "pages": page_list}

    def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        """Append blocks to a page in an allowlisted database."""
        page = self._store.page(page_id)
        if page is None:
            raise ToolError(f"page {page_id} not found")
        self._database(page["database_id"])
        block_id_list = self._store.append_blocks(page_id, validate_blocks(blocks))
        return {"page_id": page_id, "block_ids": block_id_list}

    def reset_database(self, name: str, page_list: list[dict[str, Any]]) -> dict[str, str]:
        """Insert fixture pages (the sandbox store itself is cleared by the caller)."""
        database = self._database(name)
        return {
            p["ref"]: self._store.add_page(database["id"], p["title"], p.get("properties", {}))
            for p in page_list
        }

    def pages_since(self, since: float) -> list[NotionPage]:
        """Read sandbox pages and their blocks."""
        page_list = []
        for name in self._database_names:
            database = self._store.database(name)
            if database is None:
                continue
            for page in self._store.database_pages(database["id"], since):
                block_list = [
                    NotionBlock(
                        b["type"], b["text"], None if b["checked"] is None else bool(b["checked"])
                    )
                    for b in self._store.page_blocks(page["id"])
                ]
                page_list.append(
                    NotionPage(name, page["id"], page["title"], page["properties"], block_list)
                )
        return page_list


# ============= Live Backend =============


class LiveNotionBackend:
    """
    Notion API backend for databases created by scripts/seed_notion.py.

    Parameters
    ----------
    token : str
        Integration token with access to the parent page.
    databases_dict : dict
        Logical name to {database_id, data_source_id}, from data/live_refs.json.
    schemas_dict : dict
        Logical name to (title_property, property types) from configs/connectors.yaml.
    throttle : WriteThrottle
        Minimum interval between requests.
    client : NotionClient or None
        Injected client for tests.
    """

    def __init__(
        self,
        token: str,
        databases_dict: dict[str, dict[str, str]],
        schemas_dict: dict[str, tuple[str, dict[str, str]]],
        throttle: WriteThrottle,
        client: NotionClient | None = None,
    ) -> None:
        self._client = client or NotionClient(auth=token)
        self._databases_dict = databases_dict
        self._schemas_dict = schemas_dict
        self._throttle = throttle

    def _resolve(self, name_or_id: str) -> tuple[str, dict[str, str]]:
        """Resolve an allowlisted logical name or id to (name, ids)."""
        for name, ids_dict in self._databases_dict.items():
            if name_or_id in (name, ids_dict["database_id"], ids_dict["data_source_id"]):
                return name, ids_dict
        raise ToolError(f"database {name_or_id!r} is not in the allowlist")

    def _call(self, fn, **kwargs) -> dict[str, Any]:
        """Call the Notion API with throttling and error translation."""
        self._throttle.wait()
        try:
            return fn(**kwargs)
        except APIResponseError as exc:
            raise ToolError(f"notion request failed: {exc.code}: {exc}") from exc

    def _query_all(self, data_source_id: str, filter_dict: dict[str, Any] | None) -> list[dict]:
        """Page through a data source query."""
        result_list: list[dict] = []
        cursor = None
        while True:
            kwargs: dict[str, Any] = {"data_source_id": data_source_id, "page_size": PAGE_SIZE}
            if filter_dict:
                kwargs["filter"] = filter_dict
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self._call(self._client.data_sources.query, **kwargs)
            result_list += response.get("results", [])
            if not response.get("has_more"):
                return result_list
            cursor = response.get("next_cursor")

    def create_page(
        self, parent_id: str, title: str, properties: dict[str, Any], run_id: str
    ) -> dict[str, Any]:
        """Create a page in an allowlisted data source."""
        if not title.strip():
            raise ToolError("title must not be empty")
        name, ids_dict = self._resolve(parent_id)
        title_property, schema_dict = self._schemas_dict[name]
        clean_dict = validate_properties(properties, schema_dict)
        page = self._call(
            self._client.pages.create,
            parent={"type": "data_source_id", "data_source_id": ids_dict["data_source_id"]},
            properties=to_notion_properties(title_property, title.strip(), clean_dict, schema_dict),
        )
        return {"page_id": page["id"], "url": page.get("url", "")}

    def query_database(self, database_id: str, filter: dict[str, Any] | None) -> dict[str, Any]:
        """Query an allowlisted data source with a Notion filter."""
        name, ids_dict = self._resolve(database_id)
        schema_dict = self._schemas_dict[name][1]
        page_list = [
            from_notion_page(p, schema_dict)
            for p in self._query_all(ids_dict["data_source_id"], filter)
        ]
        return {"database_id": database_id, "pages": page_list}

    def _page_database(self, page_id: str) -> str:
        """Return the logical database of a page, or raise when it is not allowlisted."""
        page = self._call(self._client.pages.retrieve, page_id=page_id)
        parent = page.get("parent", {})
        parent_id = parent.get("data_source_id") or parent.get("database_id") or ""
        return self._resolve(parent_id)[0]

    def append_blocks(self, page_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
        """Append blocks to a page whose parent is an allowlisted data source."""
        self._page_database(page_id)
        clean_list = validate_blocks(blocks)
        response = self._call(
            self._client.blocks.children.append,
            block_id=page_id,
            children=[to_notion_block(b) for b in clean_list],
        )
        return {"page_id": page_id, "block_ids": [b["id"] for b in response.get("results", [])]}

    def reset_database(self, name: str, page_list: list[dict[str, Any]]) -> dict[str, str]:
        """Move every page of a database to trash and create the fixture pages."""
        _, ids_dict = self._resolve(name)
        for page in self._query_all(ids_dict["data_source_id"], None):
            self._call(self._client.pages.update, page_id=page["id"], in_trash=True)
        title_property, schema_dict = self._schemas_dict[name]
        refs_dict = {}
        for page in page_list:
            clean_dict = validate_properties(page.get("properties", {}), schema_dict)
            created = self._call(
                self._client.pages.create,
                parent={"type": "data_source_id", "data_source_id": ids_dict["data_source_id"]},
                properties=to_notion_properties(
                    title_property, page["title"], clean_dict, schema_dict
                ),
            )
            refs_dict[page["ref"]] = created["id"]
        return refs_dict

    def _blocks(self, page_id: str) -> list[NotionBlock]:
        """Read a page's top-level blocks as simplified blocks."""
        block_list = []
        cursor = None
        while True:
            kwargs: dict[str, Any] = {"block_id": page_id, "page_size": PAGE_SIZE}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self._call(self._client.blocks.children.list, **kwargs)
            for block in response.get("results", []):
                kind = block.get("type", "")
                body = block.get(kind, {})
                block_list.append(
                    NotionBlock(kind, _plain_text(body.get("rich_text", [])), body.get("checked"))
                )
            if not response.get("has_more"):
                return block_list
            cursor = response.get("next_cursor")

    def pages_since(self, since: float) -> list[NotionPage]:
        """Read pages created since a timestamp (Notion created_time has minute precision)."""
        since_iso = datetime.fromtimestamp(since, tz=UTC).replace(second=0, microsecond=0)
        created_filter = {
            "timestamp": "created_time",
            "created_time": {"on_or_after": since_iso.isoformat()},
        }
        page_list = []
        for name, ids_dict in self._databases_dict.items():
            schema_dict = self._schemas_dict[name][1]
            for raw in self._query_all(ids_dict["data_source_id"], created_filter):
                simple = from_notion_page(raw, schema_dict)
                page_list.append(
                    NotionPage(
                        name,
                        simple["id"],
                        simple["title"],
                        simple["properties"],
                        self._blocks(raw["id"]),
                    )
                )
        return page_list
