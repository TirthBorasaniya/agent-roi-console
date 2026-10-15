"""Load workflow definitions, step classes, tasks, and the dev/held-out split."""

# ============= Standard Library =============
from functools import lru_cache
from pathlib import Path

# ============= Local =============
from roiconsole.common.config import ConfigError, config_path, load_yaml
from roiconsole.common.settings import get_settings
from roiconsole.workflows.schema import (
    StepClassCatalog,
    TaskDefinition,
    WorkflowDefinition,
)

# ============= Constants =============
WORKFLOWS_SUBDIR = "workflows"
STEP_CLASSES_FILE = "step_classes.yaml"
SPLITS_FILE = "splits.yaml"
SPLIT_NAMES = ("dev", "heldout")


# ============= Workflows =============


def workflow_files() -> list[Path]:
    """Return the sorted workflow definition files."""
    return sorted(config_path(WORKFLOWS_SUBDIR).glob("*.yaml"))


@lru_cache(maxsize=1)
def load_workflows() -> dict[str, WorkflowDefinition]:
    """
    Load every workflow definition.

    Returns
    -------
    workflows_dict : dict
        workflow_id to WorkflowDefinition, in file order.
    """
    workflows_dict: dict[str, WorkflowDefinition] = {}
    for path in workflow_files():
        definition = WorkflowDefinition.model_validate(load_yaml(path))
        if definition.workflow_id != path.stem:
            raise ConfigError(f"{path}: workflow_id must match the file name")
        workflows_dict[definition.workflow_id] = definition
    return workflows_dict


def get_workflow(workflow_id: str) -> WorkflowDefinition:
    """Return one workflow definition or raise KeyError."""
    workflows_dict = load_workflows()
    if workflow_id not in workflows_dict:
        raise KeyError(f"unknown workflow: {workflow_id}")
    return workflows_dict[workflow_id]


def workflow_path(workflow_id: str) -> Path:
    """Return the YAML path of a workflow."""
    return config_path(WORKFLOWS_SUBDIR) / f"{workflow_id}.yaml"


@lru_cache(maxsize=1)
def load_step_classes() -> StepClassCatalog:
    """Load configs/step_classes.yaml."""
    return StepClassCatalog.model_validate(load_yaml(config_path(STEP_CLASSES_FILE)))


# ============= Tasks =============


def tasks_dir() -> Path:
    """Return the eval/tasks directory."""
    return get_settings().eval_dir / "tasks"


@lru_cache(maxsize=1)
def load_tasks() -> dict[str, TaskDefinition]:
    """
    Load every task file.

    Returns
    -------
    tasks_dict : dict
        task_id to TaskDefinition, sorted by task id.
    """
    tasks_dict: dict[str, TaskDefinition] = {}
    for path in sorted(tasks_dir().glob("*.yaml")):
        task = TaskDefinition.model_validate(load_yaml(path))
        if task.task_id != path.stem:
            raise ConfigError(f"{path}: task_id must match the file name")
        tasks_dict[task.task_id] = task
    return tasks_dict


@lru_cache(maxsize=1)
def load_splits() -> dict[str, list[str]]:
    """
    Load eval/splits.yaml.

    Returns
    -------
    splits_dict : dict
        "dev" and "heldout" to sorted task id lists.
    """
    data = load_yaml(get_settings().eval_dir / SPLITS_FILE)
    splits_dict = {name: sorted(data[name]) for name in SPLIT_NAMES}
    overlap_set = set(splits_dict["dev"]) & set(splits_dict["heldout"])
    if overlap_set:
        raise ConfigError(f"tasks in both splits: {sorted(overlap_set)}")
    return splits_dict


def tasks_for(split: str, workflow_id: str | None = None) -> list[TaskDefinition]:
    """
    Return the tasks of one split, optionally for one workflow.

    Parameters
    ----------
    split : str
        "dev" or "heldout".
    workflow_id : str or None
        Restrict to one workflow.

    Returns
    -------
    task_list : list of TaskDefinition
        Sorted by task id.
    """
    if split not in SPLIT_NAMES:
        raise ValueError(f"split must be one of {SPLIT_NAMES}")
    tasks_dict = load_tasks()
    task_list = [tasks_dict[task_id] for task_id in load_splits()[split]]
    if workflow_id:
        task_list = [t for t in task_list if t.workflow_id == workflow_id]
    return task_list


def clear_caches() -> None:
    """Drop cached configs, used by tests that point settings at temp directories."""
    load_workflows.cache_clear()
    load_step_classes.cache_clear()
    load_tasks.cache_clear()
    load_splits.cache_clear()
