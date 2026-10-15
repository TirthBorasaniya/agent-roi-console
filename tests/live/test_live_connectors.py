"""Live connector test (manual): every tool once against the real Slack, Notion, and Twenty."""

# ============= Standard Library =============
import time
from contextlib import AsyncExitStack

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.workflows.registry import load_tasks
from tests.helpers import in_memory_toolbox

pytestmark = pytest.mark.live


# ============= Fixtures =============


@pytest.fixture
def live_backends(monkeypatch: pytest.MonkeyPatch):
    """Return live backends, skipping when credentials are absent."""
    monkeypatch.setenv("ROI_CONNECTOR_MODE", "live")
    monkeypatch.delenv("ROI_DATA_DIR", raising=False)
    monkeypatch.setenv("ROI_ENV_FILE", ".env")
    get_settings.cache_clear()
    settings = get_settings()
    if not (settings.slack_bot_token and settings.notion_api_key and settings.twenty_api_key):
        pytest.skip("live credentials are not configured")
    return build_backends(settings, mode="live")


# ============= Tests =============


async def test_every_tool_once_against_live_workspaces(live_backends):
    started = time.time()
    task = load_tasks()["crm_pipeline_001"]
    refs_dict = live_backends.reset(task.fixtures)
    async with AsyncExitStack() as stack:
        toolbox = await in_memory_toolbox(stack, live_backends)
        channels = await toolbox.call("slack.list_channels", {}, "live-test", "channels")
        assert {c["name"] for c in channels["channels"]} >= {"roi-sandbox-pipeline"}
        posted = await toolbox.call(
            "slack.post_message",
            {"channel": "roi-sandbox-pipeline", "text": "live connector test"},
            "live-test",
            "post",
        )
        thread = await toolbox.call(
            "slack.read_thread",
            {"channel": "roi-sandbox-pipeline", "thread_ts": posted["ts"]},
            "live-test",
            "read",
        )
        assert thread["messages"][0]["text"] == "live connector test"
        opportunities = await toolbox.call("crm.list_opportunities", {}, "live-test", "opps")
        assert len(opportunities["opportunities"]) >= 1
        contact = await toolbox.call(
            "crm.get_contact", {"id": refs_dict["p_1"]}, "live-test", "get"
        )
        assert contact["contact"]["id"] == refs_dict["p_1"]
        await toolbox.call(
            "crm.update_contact",
            {"id": refs_dict["p_1"], "fields": {"city": "Lisbon"}},
            "live-test",
            "update",
        )
        await toolbox.call(
            "crm.log_activity",
            {"contact_id": refs_dict["p_1"], "note": "pipeline live test"},
            "live-test",
            "log",
        )
        page = await toolbox.call(
            "notion.create_page",
            {
                "parent_id": "meeting_notes",
                "title": "Live Test",
                "properties": {"Date": "2026-01-02"},
            },
            "live-test",
            "create",
        )
        await toolbox.call(
            "notion.append_blocks",
            {"page_id": page["page_id"], "blocks": [{"type": "to_do", "text": "Maya: verify"}]},
            "live-test",
            "append",
        )
        queried = await toolbox.call(
            "notion.query_database",
            {
                "database_id": "meeting_notes",
                "filter": {"property": "Name", "title": {"contains": "Live"}},
            },
            "live-test",
            "query",
        )
        assert any(p["title"] == "Live Test" for p in queried["pages"])
    snapshot = live_backends.snapshot(started - 1)
    assert any(m.o_bot and m.text == "live connector test" for m in snapshot.slack_messages)
    assert any(a.contact_id == refs_dict["p_1"] for a in snapshot.crm_activities)
    assert any(p.title == "Live Test" and p.blocks for p in snapshot.notion_pages)
