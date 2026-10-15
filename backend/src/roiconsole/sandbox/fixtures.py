"""Connector configuration access and resolution of task inputs against fixture refs."""

# ============= Standard Library =============
from functools import lru_cache
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.workflows.schema import TaskDefinition
from roiconsole.workflows.templating import render_value

# ============= Constants =============
CONNECTORS_FILE = "connectors.yaml"


# ============= Config =============


@lru_cache(maxsize=1)
def load_connectors_config() -> dict[str, Any]:
    """Load configs/connectors.yaml."""
    return load_yaml(config_path(CONNECTORS_FILE))


def database_schema(name: str) -> tuple[str, dict[str, str]]:
    """
    Return the title property and property types of a logical Notion database.

    Parameters
    ----------
    name : str
        Logical database name from configs/connectors.yaml.

    Returns
    -------
    title_property : str
        Name of the title property.
    schema_dict : dict
        Property name to Notion property type.
    """
    database_dict = load_connectors_config()["notion"]["databases"][name]
    return database_dict["title_property"], dict(database_dict["properties"])


# ============= Inputs =============


def resolve_inputs(task: TaskDefinition, refs_dict: dict[str, str]) -> dict[str, Any]:
    """
    Render a task's inputs against the refs produced by a reset.

    Parameters
    ----------
    task : TaskDefinition
        Task whose inputs may contain `{{ refs.<name> }}` templates.
    refs_dict : dict
        Output of a reset.

    Returns
    -------
    inputs_dict : dict
        Inputs with concrete ids substituted.
    """
    return render_value(task.inputs, {"refs": refs_dict})
