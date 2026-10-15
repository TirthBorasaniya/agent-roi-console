"""Validator functions keyed by the names used in workflow definitions."""

# ============= Standard Library =============
import re
import unicodedata
from collections.abc import Callable
from typing import Any

# ============= Local =============
from roiconsole.validators.state import EndState, NotionPage, SlackMessage, ValidatorResult

# ============= Constants =============
DEFAULT_AMOUNT_REL_TOL = 0.005
AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?([kKmM])?(?![\w])")
AMOUNT_MULTIPLIER_DICT = {"k": 1_000.0, "m": 1_000_000.0}
PUNCTUATION_MAP = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)


# ============= Text Helpers =============


def normalize_text(text: str) -> str:
    """Casefold, normalize unicode punctuation, and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(PUNCTUATION_MAP)
    # markdown emphasis and hyphenation differences must not decide a match
    text = text.replace("*", "").replace("_", " ").replace("`", "").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def contains_term(text: str, term: str) -> bool:
    """True when the normalized term occurs in the normalized text."""
    return normalize_text(term) in normalize_text(text)


def extract_amounts(text: str) -> list[float]:
    """
    Extract every number in a text, applying k and M suffixes.

    Parameters
    ----------
    text : str
        Message text such as "Total: $128,500 (about $128.5K)".

    Returns
    -------
    amounts : list of float
        Parsed values, e.g. [128500.0, 128500.0].
    """
    amount_list = []
    for number, suffix in AMOUNT_RE.findall(text):
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        amount_list.append(value * AMOUNT_MULTIPLIER_DICT.get(suffix.lower(), 1.0))
    return amount_list


def _bot_messages(state: EndState, channel: str, thread_ts: str | None) -> list[SlackMessage]:
    """Return bot messages in a channel; in a thread when thread_ts is set, top level otherwise."""
    return [
        m
        for m in state.slack_messages
        if m.o_bot and m.channel == channel and (m.thread_ts or None) == (thread_ts or None)
    ]


def _latest_bot_message(
    state: EndState, channel: str, thread_ts: str | None
) -> SlackMessage | None:
    """Return the most recent bot message at a location."""
    message_list = _bot_messages(state, channel, thread_ts)
    return max(message_list, key=lambda m: float(m.ts)) if message_list else None


def _matching_pages(state: EndState, database: str, title_contains: str) -> list[NotionPage]:
    """Return pages in a database whose title contains a phrase."""
    return [
        p
        for p in state.notion_pages
        if p.database == database and contains_term(p.title, title_contains)
    ]


# ============= Slack Validators =============


def slack_message_posted(
    state: EndState, channel: str, thread_ts: str | None = None
) -> ValidatorResult:
    """Pass when the bot posted at least one message at the location during the run."""
    count = len(_bot_messages(state, channel, thread_ts))
    where = f"{channel} thread {thread_ts}" if thread_ts else channel
    return ValidatorResult("slack_message_posted", count >= 1, f"{count} bot message(s) in {where}")


def message_mentions_terms(
    state: EndState, channel: str, terms: list[str], thread_ts: str | None = None
) -> ValidatorResult:
    """Pass when the latest bot message at the location contains every required term."""
    message = _latest_bot_message(state, channel, thread_ts)
    if message is None:
        return ValidatorResult("message_mentions_terms", False, "no bot message found")
    missing_list = [t for t in terms if not contains_term(message.text, t)]
    return ValidatorResult(
        "message_mentions_terms",
        not missing_list,
        f"missing: {missing_list}" if missing_list else f"all {len(terms)} terms present",
    )


def message_excludes_terms(
    state: EndState, channel: str, terms: list[str], thread_ts: str | None = None
) -> ValidatorResult:
    """Pass when the latest bot message at the location contains none of the forbidden terms."""
    message = _latest_bot_message(state, channel, thread_ts)
    if message is None:
        return ValidatorResult("message_excludes_terms", False, "no bot message found")
    present_list = [t for t in terms if contains_term(message.text, t)]
    return ValidatorResult(
        "message_excludes_terms",
        not present_list,
        f"forbidden terms present: {present_list}" if present_list else "no forbidden terms",
    )


def message_mentions_amount(
    state: EndState,
    channel: str,
    amount: float,
    rel_tol: float = DEFAULT_AMOUNT_REL_TOL,
    thread_ts: str | None = None,
) -> ValidatorResult:
    """Pass when the latest bot message states a number within rel_tol of the expected amount."""
    message = _latest_bot_message(state, channel, thread_ts)
    if message is None:
        return ValidatorResult("message_mentions_amount", False, "no bot message found")
    amount_list = extract_amounts(message.text)
    o_found = any(abs(a - amount) <= rel_tol * abs(amount) for a in amount_list)
    return ValidatorResult(
        "message_mentions_amount",
        o_found,
        f"expected {amount}, found {amount_list[:12]}",
    )


# ============= Notion Validators =============


def notion_page_created(state: EndState, database: str, title_contains: str) -> ValidatorResult:
    """Pass when exactly one page whose title contains the phrase was created in the database."""
    count = len(_matching_pages(state, database, title_contains))
    return ValidatorResult(
        "notion_page_created",
        count == 1,
        f"{count} page(s) in {database} with title containing {title_contains!r}",
    )


def _normalize_property(value: Any) -> Any:
    """Normalize a property value for comparison: lists become casefolded sets."""
    if isinstance(value, list):
        return {normalize_text(str(v)) for v in value}
    if isinstance(value, dict) and "start" in value:
        return normalize_text(str(value["start"])[:10])
    if value is None:
        return None
    return normalize_text(str(value))


def notion_property_equals(
    state: EndState, database: str, title_contains: str, property: str, value: Any
) -> ValidatorResult:
    """Pass when the matching page's property equals the expected value."""
    page_list = _matching_pages(state, database, title_contains)
    if len(page_list) != 1:
        return ValidatorResult(
            "notion_property_equals", False, f"expected one matching page, found {len(page_list)}"
        )
    actual = page_list[0].properties.get(property)
    o_equal = _normalize_property(actual) == _normalize_property(value)
    return ValidatorResult(
        "notion_property_equals", o_equal, f"{property}: expected {value!r}, got {actual!r}"
    )


def notion_todos_mention(
    state: EndState, database: str, title_contains: str, terms: list[str], min_todos: int
) -> ValidatorResult:
    """Pass when the matching page has at least min_todos to-dos and every term is in one."""
    page_list = _matching_pages(state, database, title_contains)
    if len(page_list) != 1:
        return ValidatorResult(
            "notion_todos_mention", False, f"expected one matching page, found {len(page_list)}"
        )
    todo_text_list = [b.text for b in page_list[0].blocks if b.type == "to_do"]
    missing_list = [t for t in terms if not any(contains_term(x, t) for x in todo_text_list)]
    o_passed = len(todo_text_list) >= min_todos and not missing_list
    return ValidatorResult(
        "notion_todos_mention",
        o_passed,
        f"{len(todo_text_list)} to-do(s), need {min_todos}; missing terms: {missing_list}",
    )


# ============= CRM Validators =============


def crm_activity_logged(state: EndState, contact: str, contains: str) -> ValidatorResult:
    """Pass when an activity containing the phrase was logged on the expected contact only."""
    matching_list = [a for a in state.crm_activities if contains_term(a.note, contains)]
    on_contact_list = [a for a in matching_list if a.contact_id == contact]
    wrong_list = [a.contact_id for a in matching_list if a.contact_id != contact]
    o_passed = bool(on_contact_list) and not wrong_list
    return ValidatorResult(
        "crm_activity_logged",
        o_passed,
        f"{len(on_contact_list)} on expected contact; on other contacts: {wrong_list}",
    )


# ============= Registry =============

VALIDATORS_DICT: dict[str, Callable[..., ValidatorResult]] = {
    "slack_message_posted": slack_message_posted,
    "message_mentions_terms": message_mentions_terms,
    "message_excludes_terms": message_excludes_terms,
    "message_mentions_amount": message_mentions_amount,
    "notion_page_created": notion_page_created,
    "notion_property_equals": notion_property_equals,
    "notion_todos_mention": notion_todos_mention,
    "crm_activity_logged": crm_activity_logged,
}
