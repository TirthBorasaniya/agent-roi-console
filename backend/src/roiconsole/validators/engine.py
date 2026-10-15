"""Run a task's validators against an end-state snapshot read from the backing systems."""

# ============= Standard Library =============
import logging
from typing import Any, Protocol

# ============= Local =============
from roiconsole.sandbox.store import SANDBOX_BOT_USER, SandboxStore
from roiconsole.validators.library import VALIDATORS_DICT
from roiconsole.validators.state import (
    CrmActivity,
    EndState,
    NotionBlock,
    NotionPage,
    SlackMessage,
    ValidatorResult,
)
from roiconsole.workflows.schema import TaskDefinition, WorkflowDefinition
from roiconsole.workflows.templating import TemplateRenderError, render_value

# ============= Constants =============
logger = logging.getLogger(__name__)


# ============= Readers =============


class StateReader(Protocol):
    """Reads everything created since a timestamp into an EndState."""

    def snapshot(self, since: float) -> EndState:
        """Return the end state for objects created at or after `since` (epoch seconds)."""
        ...


class SandboxStateReader:
    """
    Read end state from the SQLite sandbox store.

    Parameters
    ----------
    store : SandboxStore
        Store written by the sandbox MCP backends.
    """

    def __init__(self, store: SandboxStore) -> None:
        self._store = store

    def snapshot(self, since: float) -> EndState:
        """Return messages, pages with blocks, and activities created since `since`."""
        state = EndState()
        for channel in self._store.list_channels():
            for row in self._store.channel_messages(channel["id"], since):
                state.slack_messages.append(
                    SlackMessage(
                        channel=channel["name"],
                        ts=row["ts"],
                        text=row["text"],
                        user=row["user"],
                        o_bot=bool(row["o_bot"]) and row["user"] == SANDBOX_BOT_USER,
                        thread_ts=row["thread_ts"],
                    )
                )
        with self._store.connect() as conn:
            database_rows = conn.execute("SELECT id, name FROM notion_databases").fetchall()
        for database in database_rows:
            for page in self._store.database_pages(database["id"], since):
                block_list = [
                    NotionBlock(
                        type=b["type"],
                        text=b["text"],
                        checked=None if b["checked"] is None else bool(b["checked"]),
                    )
                    for b in self._store.page_blocks(page["id"])
                ]
                state.notion_pages.append(
                    NotionPage(
                        database=database["name"],
                        page_id=page["id"],
                        title=page["title"],
                        properties=page["properties"],
                        blocks=block_list,
                    )
                )
        for activity in self._store.activities(since):
            state.crm_activities.append(
                CrmActivity(contact_id=activity["contact_id"], note=activity["note"])
            )
        return state


# ============= Engine =============


def check_task_validators(workflow: WorkflowDefinition, task: TaskDefinition) -> None:
    """
    Require that a task configures exactly the validators its workflow declares.

    Raises
    ------
    ValueError
        When names differ or a name is not registered.
    """
    task_name_list = [v.name for v in task.expected.validators]
    if sorted(task_name_list) != sorted(workflow.validators):
        raise ValueError(
            f"{task.task_id}: validators {task_name_list} differ from {workflow.validators}"
        )
    unknown_list = [n for n in task_name_list if n not in VALIDATORS_DICT]
    if unknown_list:
        raise ValueError(f"{task.task_id}: unknown validators {unknown_list}")


def run_validators(
    task: TaskDefinition,
    state: EndState,
    refs_dict: dict[str, str],
    inputs_dict: dict[str, Any],
) -> list[ValidatorResult]:
    """
    Evaluate every validator configured for a task.

    Parameters
    ----------
    task : TaskDefinition
        Task with expected validator specs.
    state : EndState
        Snapshot read after the run.
    refs_dict : dict
        Fixture refs from the reset, used to render validator args.
    inputs_dict : dict
        Resolved task inputs, also available to validator args.

    Returns
    -------
    results : list of ValidatorResult
        One result per validator; a validator that raises is recorded as failed.
    """
    result_list = []
    context_dict = {"refs": refs_dict, "inputs": inputs_dict}
    for spec in task.expected.validators:
        validator = VALIDATORS_DICT[spec.name]
        try:
            args_dict = render_value(spec.args, context_dict)
            result_list.append(validator(state, **args_dict))
        except (TemplateRenderError, TypeError, ValueError) as exc:
            logger.warning(f"validator {spec.name} errored on {task.task_id}: {exc}")
            result_list.append(ValidatorResult(spec.name, False, f"validator error: {exc}"))
    return result_list


def all_passed(result_list: list[ValidatorResult]) -> bool:
    """True when every validator passed and at least one ran."""
    return bool(result_list) and all(r.passed for r in result_list)
