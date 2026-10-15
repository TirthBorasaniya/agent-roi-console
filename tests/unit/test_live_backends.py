"""Unit tests for the live Slack, Notion, and Twenty backends with fake API clients."""

# ============= Standard Library =============
import json
from unittest.mock import MagicMock

# ============= Third-Party =============
import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

# ============= Local =============
from roiconsole.mcp_servers.backends.notion import (
    LiveNotionBackend,
    from_notion_page,
    page_matches_filter,
    to_notion_block,
    to_notion_properties,
    validate_properties,
)
from roiconsole.mcp_servers.backends.slack import OUTPUT_EVENT_TYPE, LiveSlackBackend
from roiconsole.mcp_servers.backends.twenty import TwentyClient, TwentyCrmBackend
from roiconsole.mcp_servers.common import WriteThrottle

pytestmark = pytest.mark.unit


# ============= Slack =============


def fake_slack_client():
    """Return a WebClient double with two visible channels."""
    client = MagicMock()
    client.retry_handlers = []
    client.conversations_list.return_value = {
        "channels": [{"name": "roi-sandbox-digest", "id": "C1"}, {"name": "general", "id": "C9"}],
        "response_metadata": {"next_cursor": ""},
    }
    client.chat_postMessage.return_value = {"ts": "1700.1"}
    return client


def test_live_slack_post_attaches_output_metadata_and_respects_allowlist():
    client = fake_slack_client()
    backend = LiveSlackBackend("t", ["roi-sandbox-digest"], WriteThrottle(0), client=client)
    result = backend.post_message("roi-sandbox-digest", "hello", None, "run1")
    assert result == {"channel": "roi-sandbox-digest", "ts": "1700.1"}
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C1"
    assert kwargs["metadata"]["event_type"] == OUTPUT_EVENT_TYPE
    assert kwargs["metadata"]["event_payload"]["run_id"] == "run1"
    with pytest.raises(ToolError, match="allowlist"):
        backend.post_message("general", "x", None, "run1")


def test_live_slack_snapshot_marks_only_console_output_as_bot():
    client = fake_slack_client()
    client.conversations_history.return_value = {
        "messages": [
            {"ts": "1.0", "text": "fixture", "username": "Maya", "metadata": {"event_type": "x"}},
            {"ts": "2.0", "text": "digest", "metadata": {"event_type": OUTPUT_EVENT_TYPE}},
        ],
        "response_metadata": {},
    }
    backend = LiveSlackBackend("t", ["roi-sandbox-digest"], WriteThrottle(0), client=client)
    message_list = backend.messages_since(0.0)
    assert [(m.text, m.o_bot) for m in message_list] == [("fixture", False), ("digest", True)]
    assert client.conversations_history.call_args.kwargs["include_all_metadata"] is True


# ============= Notion =============


SCHEMA = {"Date": "date", "Attendees": "multi_select", "Summary": "rich_text"}


def test_notion_property_conversion_round_trip():
    clean = validate_properties({"Date": "2026-01-02", "Attendees": ["Maya"]}, SCHEMA)
    api_props = to_notion_properties("Name", "Sync", clean, SCHEMA)
    assert api_props["Date"] == {"date": {"start": "2026-01-02"}}
    page = {
        "id": "p",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Sync"}]},
            "Date": {"type": "date", "date": {"start": "2026-01-02"}},
            "Attendees": {"type": "multi_select", "multi_select": [{"name": "Maya"}]},
        },
    }
    assert from_notion_page(page, SCHEMA)["properties"] == clean


def test_notion_validation_rejects_unknown_and_malformed_properties():
    with pytest.raises(ToolError):
        validate_properties({"Owner": "x"}, SCHEMA)
    with pytest.raises(ToolError):
        validate_properties({"Attendees": "Maya"}, SCHEMA)


def test_notion_block_conversion():
    block = to_notion_block({"type": "to_do", "text": "Maya: ship", "checked": False})
    assert block["to_do"]["checked"] is False
    assert block["to_do"]["rich_text"][0]["text"]["content"] == "Maya: ship"


def test_sandbox_filter_subset():
    page = {"title": "Runbook", "properties": {"Tags": ["infrastructure"], "Date": "2026-01-05"}}
    assert page_matches_filter(
        page, {"property": "Tags", "multi_select": {"contains": "Infrastructure"}}
    )
    assert page_matches_filter(
        page,
        {
            "and": [
                {"property": "Name", "title": {"contains": "run"}},
                {"property": "Date", "date": {"on_or_after": "2026-01-01"}},
            ]
        },
    )
    with pytest.raises(ToolError):
        page_matches_filter(page, {"property": "Tags", "multi_select": {"is_empty": True}})


def test_live_notion_create_page_targets_data_source_and_checks_allowlist():
    client = MagicMock()
    client.pages.create.return_value = {"id": "page1", "url": "https://notion.so/page1"}
    backend = LiveNotionBackend(
        "t",
        {"meeting_notes": {"database_id": "db1", "data_source_id": "ds1"}},
        {"meeting_notes": ("Name", {"Date": "date", "Attendees": "multi_select"})},
        WriteThrottle(0),
        client=client,
    )
    result = backend.create_page("meeting_notes", "Sync", {"Date": "2026-01-02"}, "run")
    assert result["page_id"] == "page1"
    parent = client.pages.create.call_args.kwargs["parent"]
    assert parent == {"type": "data_source_id", "data_source_id": "ds1"}
    with pytest.raises(ToolError, match="allowlist"):
        backend.create_page("other_db", "x", {}, "run")


# ============= Twenty =============


def twenty_backend(handler):
    """Return a Twenty backend whose HTTP calls go to a handler."""
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = TwentyClient("http://twenty:3000", "key", http_client=http)
    client._throttle = WriteThrottle(0)
    return TwentyCrmBackend(client, ["NEW", "SCREENING", "MEETING", "PROPOSAL"], "roi-sandbox")


def test_twenty_list_opportunities_scopes_to_sandbox_companies_and_converts_amounts():
    seen_list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_list.append(body)
        assert request.headers["Authorization"] == "Bearer key"
        if "SandboxCompanies" in body["query"]:
            return httpx.Response(
                200, json={"data": {"companies": {"edges": [{"node": {"id": "c1"}}]}}}
            )
        node = {
            "id": "o1",
            "name": "Acme Renewal 1",
            "stage": "PROPOSAL",
            "amount": {"amountMicros": 12_500_000_000, "currencyCode": "USD"},
            "company": {"id": "c1", "name": "Acme"},
            "pointOfContact": {"id": "p1", "name": {"firstName": "Maya", "lastName": "Chen"}},
        }
        return httpx.Response(200, json={"data": {"opportunities": {"edges": [{"node": node}]}}})

    result = twenty_backend(handler).list_opportunities("proposal")
    assert result["opportunities"][0]["amount"] == 12_500.0
    assert result["opportunities"][0]["point_of_contact"] == {"id": "p1", "name": "Maya Chen"}
    assert seen_list[1]["variables"]["stages"] == ["PROPOSAL"]
    assert seen_list[1]["variables"]["companies"] == ["c1"]


def test_twenty_log_activity_creates_note_and_target_for_sandbox_contact_only():
    operations_list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        operations_list.append(body["query"].split("(")[0].strip())
        if "query Person" in body["query"]:
            email = (
                "maya@acme.roi-sandbox.test" if body["variables"]["id"] == "p1" else "x@real.com"
            )
            node = {"id": body["variables"]["id"], "emails": {"primaryEmail": email}, "name": {}}
            return httpx.Response(200, json={"data": {"person": node}})
        if "createNote(" in body["query"]:
            assert body["variables"]["data"]["bodyV2"] == {"markdown": "pipeline review"}
            return httpx.Response(200, json={"data": {"createNote": {"id": "n1"}}})
        assert body["variables"]["data"] == {"noteId": "n1", "targetPersonId": "p1"}
        return httpx.Response(200, json={"data": {"createNoteTarget": {"id": "t1"}}})

    backend = twenty_backend(handler)
    assert backend.log_activity("p1", "pipeline review", "run") == {
        "activity_id": "n1",
        "contact_id": "p1",
    }
    with pytest.raises(ToolError, match="outside the sandbox"):
        backend.log_activity("p2", "note", "run")


def test_twenty_graphql_errors_raise_tool_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Field 'x' not found"}]})

    with pytest.raises(ToolError, match="Field 'x' not found"):
        twenty_backend(handler).list_new_leads("2026-01-01T00:00:00Z")


def test_twenty_reset_maps_refs_in_order():
    created_dict = {"createCompanies": 0, "createPeople": 0, "createOpportunities": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query = body["query"]
        for name in created_dict:
            if f"{name}(" in query:
                rows = body["variables"]["data"]
                created_dict[name] += len(rows)
                return httpx.Response(
                    200, json={"data": {name: [{"id": f"{name}-{i}"} for i in range(len(rows))]}}
                )
        empty = {"edges": [], "pageInfo": {"hasNextPage": False}}
        return httpx.Response(200, json={"data": {"companies": empty, "people": empty}})

    fixtures = {
        "companies": [{"ref": "co_1", "name": "Acme", "domain": "acme.roi-sandbox.test"}],
        "people": [
            {
                "ref": "p_1",
                "first_name": "M",
                "last_name": "C",
                "email": "m@acme.roi-sandbox.test",
                "company": "co_1",
            }
        ],
        "opportunities": [
            {
                "ref": "opp_1",
                "name": "Deal",
                "stage": "NEW",
                "amount": 1000,
                "company": "co_1",
                "point_of_contact": "p_1",
            }
        ],
    }
    refs_dict = twenty_backend(handler).reset(fixtures)
    assert refs_dict == {
        "co_1": "createCompanies-0",
        "p_1": "createPeople-0",
        "opp_1": "createOpportunities-0",
    }
