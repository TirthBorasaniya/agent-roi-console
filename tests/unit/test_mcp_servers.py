"""Unit tests for the three MCP servers over sandbox backends through in-memory sessions."""

# ============= Standard Library =============
from contextlib import AsyncExitStack

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.db import connect, init_db
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.runner.mcp_client import ToolCallError, ToolCallRecorder
from roiconsole.workflows.registry import load_tasks
from tests.helpers import in_memory_toolbox

pytestmark = pytest.mark.unit


# ============= Fixtures =============


@pytest.fixture
def backends():
    """Return dryrun backends."""
    return build_backends()


# ============= Tests =============


async def test_tool_catalog_matches_spec_and_hides_idempotency_key(backends):
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends)
        specs_dict = await toolbox.list_tools()
        assert set(specs_dict) == {
            "slack.post_message",
            "slack.list_channels",
            "slack.read_thread",
            "notion.create_page",
            "notion.query_database",
            "notion.append_blocks",
            "crm.list_new_leads",
            "crm.get_contact",
            "crm.update_contact",
            "crm.log_activity",
            "crm.list_opportunities",
        }
        write_set = {name for name, spec in specs_dict.items() if spec.o_write}
        assert write_set == {
            "slack.post_message",
            "notion.create_page",
            "notion.append_blocks",
            "crm.update_contact",
            "crm.log_activity",
        }
        schema = specs_dict["slack.post_message"].openai_schema()
        assert schema["function"]["name"] == "slack__post_message"
        assert "idempotency_key" not in schema["function"]["parameters"]["properties"]


async def test_slack_post_is_idempotent_and_recorded(backends, tmp_path):
    init_db()
    backends.reset(load_tasks()["slack_digest_001"].fixtures)
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends, ToolCallRecorder())
        args = {"channel": "roi-sandbox-digest", "text": "digest"}
        first = await toolbox.call("slack.post_message", args, "run1", "post_digest")
        second = await toolbox.call("slack__post_message", args, "run1", "post_digest")
        assert first["ts"] == second["ts"] and second["idempotent_replay"]
    snapshot = backends.snapshot(0.0)
    assert len([m for m in snapshot.slack_messages if m.o_bot]) == 1
    with connect() as conn:
        rows = conn.execute("SELECT run_id, step_id, status FROM tool_calls").fetchall()
    assert [tuple(r) for r in rows] == [("run1", "post_digest", "ok")] * 2


async def test_allowlist_rejects_unknown_channel_and_database(backends):
    backends.reset({})
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends)
        with pytest.raises(ToolCallError, match="allowlist"):
            await toolbox.call("slack.post_message", {"channel": "general", "text": "x"}, "r", "s")
        with pytest.raises(ToolCallError, match="allowlist"):
            await toolbox.call("notion.query_database", {"database_id": "secret_db"}, "r", "s")


async def test_slack_read_thread_returns_fixture_messages(backends):
    task = load_tasks()["slack_digest_001"]
    refs_dict = backends.reset(task.fixtures)
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends)
        result = await toolbox.call(
            "slack.read_thread",
            {"channel": "roi-sandbox-eng", "thread_ts": refs_dict["thread_main"]},
            "r",
            "s",
        )
    expected_list = task.fixtures["slack"]["threads"][0]["messages"]
    assert [m["text"] for m in result["messages"]] == [m["text"] for m in expected_list]


async def test_notion_query_filters_by_tag_and_pages_get_blocks(backends):
    task = load_tasks()["notion_search_001"]
    backends.reset(task.fixtures)
    tag = task.expected.gold_outputs["extract_filter"]["tag"]
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends)
        result = await toolbox.call(
            "notion.query_database",
            {
                "database_id": "knowledge_base",
                "filter": {"property": "Tags", "multi_select": {"contains": tag}},
            },
            "r",
            "s",
        )
        gold_titles = {p["title"] for p in task.expected.gold_outputs["query_kb"]["pages"]}
        assert {p["title"] for p in result["pages"]} == gold_titles
        page = await toolbox.call(
            "notion.create_page",
            {
                "parent_id": "meeting_notes",
                "title": "Sync",
                "properties": {"Date": "2026-01-02", "Attendees": ["Maya"]},
            },
            "r",
            "create",
        )
        await toolbox.call(
            "notion.append_blocks",
            {"page_id": page["page_id"], "blocks": [{"type": "to_do", "text": "Maya: ship"}]},
            "r",
            "append",
        )
        with pytest.raises(ToolCallError, match="date"):
            await toolbox.call(
                "notion.create_page",
                {"parent_id": "meeting_notes", "title": "Bad", "properties": {"Date": "Jan 2"}},
                "r",
                "bad",
            )
    pages_list = [p for p in backends.snapshot(0.0).notion_pages if p.database == "meeting_notes"]
    assert pages_list[0].properties == {"Date": "2026-01-02", "Attendees": ["Maya"]}
    assert pages_list[0].blocks[0].type == "to_do"


async def test_crm_tools_list_update_and_log(backends):
    task = load_tasks()["crm_pipeline_002"]
    refs_dict = backends.reset(task.fixtures)
    stage = task.inputs["stage"]
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, backends)
        result = await toolbox.call("crm.list_opportunities", {"stage": stage}, "r", "s")
        expected_set = {
            o["name"] for o in task.fixtures["crm"]["opportunities"] if o["stage"] == stage
        }
        assert {o["name"] for o in result["opportunities"]} == expected_set
        contact_id = refs_dict["p_1"]
        updated = await toolbox.call(
            "crm.update_contact", {"id": contact_id, "fields": {"city": "Lisbon"}}, "r", "u"
        )
        assert updated["contact"]["city"] == "Lisbon"
        await toolbox.call(
            "crm.log_activity", {"contact_id": contact_id, "note": "pipeline"}, "r", "l"
        )
        leads = await toolbox.call("crm.list_new_leads", {"since": "2000-01-01T00:00:00"}, "r", "n")
        assert len(leads["leads"]) == len(task.fixtures["crm"]["people"])
        with pytest.raises(ToolCallError, match="unknown stage"):
            await toolbox.call("crm.list_opportunities", {"stage": "WON"}, "r", "s")
        with pytest.raises(ToolCallError, match="cannot update"):
            await toolbox.call(
                "crm.update_contact", {"id": contact_id, "fields": {"email": "x"}}, "r", "u2"
            )
    activities_list = backends.snapshot(0.0).crm_activities
    assert [(a.contact_id, a.note) for a in activities_list] == [(contact_id, "pipeline")]
