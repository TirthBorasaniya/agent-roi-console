"""Create the sandbox Notion databases under NOTION_PARENT_PAGE_ID and record their ids."""

# ============= Standard Library =============
import json

# ============= Third-Party =============
from notion_client import Client as NotionClient

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.task_data import KB_TAGS
from roiconsole.mcp_servers.backends.factory import write_live_refs
from roiconsole.sandbox.fixtures import database_schema, load_connectors_config

# ============= Constants =============
DATABASE_TITLES = {
    "knowledge_base": "ROI Sandbox Knowledge Base",
    "meeting_notes": "ROI Sandbox Meetings",
}


# ============= Helpers =============


def property_schema(name: str) -> dict:
    """Return the Notion data source property schema for a logical database."""
    title_property, schema_dict = database_schema(name)
    properties_dict: dict = {title_property: {"title": {}}}
    for prop, kind in schema_dict.items():
        if kind == "multi_select":
            options = [{"name": t} for t in KB_TAGS] if prop == "Tags" else []
            properties_dict[prop] = {"multi_select": {"options": options}}
        else:
            properties_dict[prop] = {kind: {}}
    return properties_dict


# ============= Entry Point =============


def main() -> None:
    """Create each configured database once and write data/live_refs.json."""
    settings = get_settings()
    if not settings.notion_api_key or not settings.notion_parent_page_id:
        raise SystemExit("NOTION_API_KEY and NOTION_PARENT_PAGE_ID must be set")
    client = NotionClient(auth=settings.notion_api_key)
    databases_dict = {}
    for name in load_connectors_config()["notion"]["databases"]:
        created = client.databases.create(
            parent={"type": "page_id", "page_id": settings.notion_parent_page_id},
            title=[{"type": "text", "text": {"content": DATABASE_TITLES[name]}}],
            initial_data_source={"properties": property_schema(name)},
        )
        databases_dict[name] = {
            "database_id": created["id"],
            "data_source_id": created["data_sources"][0]["id"],
        }
        print(f"created {name}: {created['id']}")
    path = write_live_refs(settings, {"notion": databases_dict})
    print(f"wrote {path}: {json.dumps(databases_dict)}")


if __name__ == "__main__":
    main()
