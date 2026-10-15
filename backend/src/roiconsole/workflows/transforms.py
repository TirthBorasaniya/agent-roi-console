"""Deterministic transform steps referenced by name from workflow definitions."""

# ============= Standard Library =============
from collections.abc import Callable
from typing import Any

# ============= Constants =============
DECISIONS_HEADING = "Decisions"
ACTION_ITEMS_HEADING = "Action items"


# ============= Transforms =============


def meeting_blocks(decisions: list[str], action_items: list[dict[str, Any]]) -> list[dict]:
    """
    Build simplified Notion blocks for a meeting page.

    Parameters
    ----------
    decisions : list of str
        Decision sentences.
    action_items : list of dict
        Each dict has owner, task, and due.

    Returns
    -------
    blocks : list of dict
        Simplified blocks: {"type": "heading_2" | "paragraph" | "to_do", "text": str}.
    """
    if not isinstance(decisions, list) or not isinstance(action_items, list):
        raise ValueError("decisions and action_items must be lists")
    block_list: list[dict] = [{"type": "heading_2", "text": DECISIONS_HEADING}]
    block_list += [{"type": "paragraph", "text": str(d)} for d in decisions]
    block_list.append({"type": "heading_2", "text": ACTION_ITEMS_HEADING})
    for item in action_items:
        if not isinstance(item, dict):
            raise ValueError(f"action item is not an object: {item!r}")
        owner = item.get("owner", "")
        task = item.get("task", "")
        due = item.get("due", "")
        text = f"{owner}: {task}" + (f" (due {due})" if due else "")
        block_list.append({"type": "to_do", "text": text, "checked": False})
    return block_list


TRANSFORMS_DICT: dict[str, Callable[..., Any]] = {"meeting_blocks": meeting_blocks}


def run_transform(name: str, args_dict: dict[str, Any]) -> Any:
    """Run a registered transform by name."""
    if name not in TRANSFORMS_DICT:
        raise KeyError(f"unknown transform: {name}")
    return TRANSFORMS_DICT[name](**args_dict)
