"""Unit tests for validators on hand-built expected and failing end states."""

# ============= Standard Library =============
import copy

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.validators.engine import all_passed, run_validators
from roiconsole.validators.library import (
    crm_activity_logged,
    extract_amounts,
    message_excludes_terms,
    message_mentions_amount,
    message_mentions_terms,
    notion_page_created,
    notion_property_equals,
    notion_todos_mention,
    slack_message_posted,
)
from roiconsole.validators.state import (
    CrmActivity,
    EndState,
    NotionBlock,
    NotionPage,
    SlackMessage,
)
from roiconsole.workflows.registry import load_tasks

pytestmark = pytest.mark.unit


# ============= Helpers =============


def bot_message(channel: str, text: str, thread_ts: str | None = None, ts: str = "10.0"):
    """Return a bot message for a hand-built state."""
    return SlackMessage(
        channel=channel, ts=ts, text=text, user="roi-console-bot", o_bot=True, thread_ts=thread_ts
    )


def expected_state_for(task, refs_dict: dict[str, str]) -> EndState:
    """
    Hand-build the end state a correct run of a task would leave.

    Built from the task's expected validator arguments, independently of any
    agent output, so it tests the validators rather than a path.
    """
    args_by_name = {v.name: v.args for v in task.expected.validators}
    state = EndState()
    if task.workflow_id == "slack_digest":
        terms = args_by_name["message_mentions_terms"]["terms"]
        state.slack_messages.append(
            bot_message("roi-sandbox-digest", "Digest: " + "; ".join(terms))
        )
    elif task.workflow_id == "notion_search":
        thread_ts = refs_dict["question_thread"]
        text = "Relevant pages: " + ", ".join(args_by_name["message_mentions_terms"]["terms"])
        state.slack_messages.append(bot_message("roi-sandbox-questions", text, thread_ts=thread_ts))
    elif task.workflow_id == "meeting_notes":
        title = args_by_name["notion_page_created"]["title_contains"]
        owner_list = args_by_name["notion_todos_mention"]["terms"]
        state.notion_pages.append(
            NotionPage(
                database="meeting_notes",
                page_id="page-1",
                title=title,
                properties={"Date": args_by_name["notion_property_equals"]["value"]},
                blocks=[
                    NotionBlock("to_do", f"{o}: follow up (due Oct 1)", False) for o in owner_list
                ],
            )
        )
    elif task.workflow_id == "crm_pipeline":
        names = args_by_name["message_mentions_terms"]["terms"]
        amount = args_by_name["message_mentions_amount"]["amount"]
        text = "Pipeline: " + ", ".join(names) + f". Total ${amount:,.0f}."
        state.slack_messages.append(bot_message("roi-sandbox-pipeline", text))
        contact_template = args_by_name["crm_activity_logged"]["contact"]
        contact_ref = contact_template.replace("{{ refs.", "").replace(" }}", "")
        state.crm_activities.append(CrmActivity(refs_dict[contact_ref], "pipeline review posted"))
    return state


def fake_refs(task) -> dict[str, str]:
    """Return refs for every fixture ref a task defines."""
    refs_dict = {}
    for thread in task.fixtures.get("slack", {}).get("threads", []):
        refs_dict[thread["ref"]] = "1700000000.000100"
    for section in ("companies", "people", "opportunities"):
        for record in task.fixtures.get("crm", {}).get(section, []):
            refs_dict[record["ref"]] = f"id-{record['ref']}"
    return refs_dict


# ============= Task-Level Tests =============


def test_every_task_passes_on_its_hand_built_expected_state():
    for task in load_tasks().values():
        refs_dict = fake_refs(task)
        state = expected_state_for(task, refs_dict)
        result_list = run_validators(task, state, refs_dict, task.inputs)
        assert all_passed(result_list), (task.task_id, [r.to_dict() for r in result_list])


def test_every_task_fails_on_an_empty_state():
    for task in load_tasks().values():
        result_list = run_validators(task, EndState(), fake_refs(task), task.inputs)
        assert not all_passed(result_list), task.task_id


def test_dropping_one_required_term_fails_slack_digest():
    task = load_tasks()["slack_digest_001"]
    terms = next(v for v in task.expected.validators if v.name == "message_mentions_terms").args[
        "terms"
    ]
    state = EndState(slack_messages=[bot_message("roi-sandbox-digest", "; ".join(terms[1:]))])
    assert not all_passed(run_validators(task, state, {}, task.inputs))


def test_all_passed_is_false_for_no_validators():
    assert not all_passed([])


# ============= Slack Validators =============


def test_slack_message_posted_ignores_human_and_wrong_location_messages():
    human = SlackMessage("c", "1.0", "hi", "Maya", o_bot=False)
    in_thread = bot_message("c", "reply", thread_ts="1.0")
    state = EndState(slack_messages=[human, in_thread])
    assert not slack_message_posted(state, "c").passed
    assert slack_message_posted(state, "c", thread_ts="1.0").passed


def test_message_mentions_terms_normalizes_case_markdown_and_dashes():
    state = EndState(slack_messages=[bot_message("c", "*Postgres 16* chosen — 30 day retention")])
    assert message_mentions_terms(state, "c", ["postgres 16", "30-day retention"]).passed
    assert not message_mentions_terms(state, "c", ["MySQL"]).passed


def test_mentions_uses_latest_bot_message():
    state = EndState(
        slack_messages=[
            bot_message("c", "alpha beta", ts="1.0"),
            bot_message("c", "beta", ts="2.0"),
        ]
    )
    assert not message_mentions_terms(state, "c", ["alpha"]).passed


def test_message_excludes_terms():
    state = EndState(slack_messages=[bot_message("c", "See Refund Approval Rules")])
    assert message_excludes_terms(state, "c", ["Invoice Schedule"]).passed
    assert not message_excludes_terms(state, "c", ["refund approval rules"]).passed


@pytest.mark.parametrize(
    "text,amount,expected",
    [
        ("Total: $128,500.", 128_500, True),
        ("about $128.5K overall", 128_500, True),
        ("total 1.2M", 1_200_000, True),
        ("total $120,000", 128_500, False),
    ],
)
def test_message_mentions_amount(text, amount, expected):
    state = EndState(slack_messages=[bot_message("c", text)])
    assert message_mentions_amount(state, "c", amount).passed is expected


def test_extract_amounts_handles_separators_and_suffixes():
    assert extract_amounts("$1,250 and 3k and 2.5M") == [1250.0, 3000.0, 2_500_000.0]


# ============= Notion Validators =============


def make_page(title: str = "Platform Weekly Sync", blocks=None, date="2026-03-04") -> NotionPage:
    """Return a meeting page."""
    return NotionPage("meeting_notes", "p1", title, {"Date": date}, blocks or [])


def test_notion_page_created_requires_exactly_one_match():
    title = "Platform Weekly Sync"
    assert notion_page_created(EndState(notion_pages=[make_page()]), "meeting_notes", title).passed
    two = EndState(notion_pages=[make_page(), make_page()])
    assert not notion_page_created(two, "meeting_notes", title).passed
    assert not notion_page_created(EndState(), "meeting_notes", title).passed


def test_notion_property_equals_compares_dates_and_lists():
    state = EndState(notion_pages=[make_page(date={"start": "2026-03-04"})])
    assert notion_property_equals(state, "meeting_notes", "Platform", "Date", "2026-03-04").passed
    assert not notion_property_equals(
        state, "meeting_notes", "Platform", "Date", "2026-03-05"
    ).passed


def test_notion_todos_mention_counts_only_todos():
    blocks = [NotionBlock("paragraph", "Maya: decide"), NotionBlock("to_do", "Omar: ship", False)]
    state = EndState(notion_pages=[make_page(blocks=blocks)])
    assert notion_todos_mention(state, "meeting_notes", "Platform", ["Omar"], 1).passed
    assert not notion_todos_mention(state, "meeting_notes", "Platform", ["Maya"], 1).passed
    assert not notion_todos_mention(state, "meeting_notes", "Platform", ["Omar"], 2).passed


# ============= CRM Validators =============


def test_crm_activity_logged_rejects_activity_on_wrong_contact():
    right = CrmActivity("p1", "Pipeline review posted")
    wrong = CrmActivity("p2", "pipeline review posted")
    assert crm_activity_logged(EndState(crm_activities=[right]), "p1", "pipeline").passed
    assert not crm_activity_logged(EndState(crm_activities=[right, wrong]), "p1", "pipeline").passed
    assert not crm_activity_logged(EndState(crm_activities=[wrong]), "p1", "pipeline").passed


def test_validator_errors_are_recorded_as_failures():
    task = copy.deepcopy(load_tasks()["crm_pipeline_001"])
    result_list = run_validators(task, EndState(), {}, task.inputs)
    crm_result = next(r for r in result_list if r.name == "crm_activity_logged")
    assert not crm_result.passed
    assert "validator error" in crm_result.detail
