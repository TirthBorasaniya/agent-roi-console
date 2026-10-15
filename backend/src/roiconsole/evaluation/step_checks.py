"""Deterministic step-level checks for E1, applied to one LLM step's output."""

# ============= Standard Library =============
from collections.abc import Callable
from typing import Any

# ============= Local =============
from roiconsole.validators.library import (
    DEFAULT_AMOUNT_REL_TOL,
    contains_term,
    extract_amounts,
    normalize_text,
)

# ============= Checks =============


def _text(output: Any) -> str:
    """Return a text output, or its JSON form for structured outputs."""
    return output if isinstance(output, str) else str(output)


def _field(output: Any, field: str) -> Any:
    """Return a field of a JSON output, or raise ValueError."""
    if not isinstance(output, dict) or field not in output:
        raise ValueError(f"output has no field {field!r}")
    return output[field]


def text_mentions_terms(output: Any, terms: list[str]) -> tuple[bool, str]:
    """Every term occurs in the text."""
    missing_list = [t for t in terms if not contains_term(_text(output), t)]
    return not missing_list, f"missing {missing_list}"


def text_excludes_terms(output: Any, terms: list[str]) -> tuple[bool, str]:
    """No forbidden term occurs in the text."""
    present_list = [t for t in terms if contains_term(_text(output), t)]
    return not present_list, f"present {present_list}"


def text_mentions_amount(
    output: Any, amount: float, rel_tol: float = DEFAULT_AMOUNT_REL_TOL
) -> tuple[bool, str]:
    """The text states a number within rel_tol of the amount."""
    amount_list = extract_amounts(_text(output))
    return any(
        abs(a - amount) <= rel_tol * abs(amount) for a in amount_list
    ), f"found {amount_list[:10]}"


def json_field_equals(output: Any, field: str, value: Any) -> tuple[bool, str]:
    """A JSON field equals the value (case and whitespace insensitive)."""
    actual = _field(output, field)
    return normalize_text(str(actual)) == normalize_text(str(value)), f"{field}={actual!r}"


def json_field_contains(output: Any, field: str, value: str) -> tuple[bool, str]:
    """A JSON string field contains the value."""
    actual = _field(output, field)
    return contains_term(str(actual), value), f"{field}={actual!r}"


def json_list_equals(output: Any, field: str, value: list[str]) -> tuple[bool, str]:
    """A JSON list field holds exactly the given items, in any order."""
    actual = _field(output, field)
    if not isinstance(actual, list):
        return False, f"{field} is not a list"
    return {normalize_text(str(a)) for a in actual} == {
        normalize_text(v) for v in value
    }, f"{field}={actual!r}"


def json_list_field_equals(output: Any, field: str, key: str, value: list[str]) -> tuple[bool, str]:
    """The `key` of each item in a JSON list field forms exactly the given set."""
    actual = _field(output, field)
    if not isinstance(actual, list) or not all(isinstance(a, dict) for a in actual):
        return False, f"{field} is not a list of objects"
    found_set = {normalize_text(str(a.get(key, ""))) for a in actual}
    return found_set == {normalize_text(v) for v in value}, f"{field}.{key}={sorted(found_set)}"


CHECKS_DICT: dict[str, Callable[..., tuple[bool, str]]] = {
    "text_mentions_terms": text_mentions_terms,
    "text_excludes_terms": text_excludes_terms,
    "text_mentions_amount": text_mentions_amount,
    "json_field_equals": json_field_equals,
    "json_field_contains": json_field_contains,
    "json_list_equals": json_list_equals,
    "json_list_field_equals": json_list_field_equals,
}


def run_check(name: str, output: Any, args_dict: dict[str, Any]) -> tuple[bool, str]:
    """Run one check; a malformed output fails the check rather than raising."""
    try:
        return CHECKS_DICT[name](output, **args_dict)
    except ValueError as exc:
        return False, str(exc)
