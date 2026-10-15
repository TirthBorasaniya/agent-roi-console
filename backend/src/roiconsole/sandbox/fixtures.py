"""Load task fixtures into the sandbox store and resolve logical references."""

# ============= Standard Library =============
from functools import lru_cache
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.sandbox.store import SandboxStore
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


# ============= Sandbox Reset =============


def load_sandbox_fixtures(store: SandboxStore, fixtures_dict: dict[str, Any]) -> dict[str, str]:
    """
    Reset the sandbox and load one task's fixtures.

    Parameters
    ----------
    store : SandboxStore
        Target store; all existing state is deleted first.
    fixtures_dict : dict
        The task's `fixtures` mapping with optional slack, notion, and crm sections.

    Returns
    -------
    refs_dict : dict
        Logical fixture ref to concrete id (thread ts, page id, record id).
    """
    store.reset()
    refs_dict: dict[str, str] = {}
    connectors_dict = load_connectors_config()

    for channel_name in connectors_dict["slack"]["channel_allowlist"]:
        store.ensure_channel(channel_name)
    for thread in fixtures_dict.get("slack", {}).get("threads", []):
        channel_id = store.ensure_channel(thread["channel"])
        message_list = thread["messages"]
        parent_ts = store.add_message(
            channel_id, message_list[0]["text"], user=message_list[0]["user"], o_bot=False
        )
        for message in message_list[1:]:
            store.add_message(
                channel_id, message["text"], user=message["user"], thread_ts=parent_ts, o_bot=False
            )
        refs_dict[thread["ref"]] = parent_ts

    for name in connectors_dict["notion"]["databases"]:
        title_property, schema_dict = database_schema(name)
        store.ensure_database(name, title_property, schema_dict)
    for database in fixtures_dict.get("notion", {}).get("databases", []):
        database_row = store.database(database["name"])
        if database_row is None:
            raise KeyError(f"fixture uses unknown Notion database {database['name']}")
        for page in database.get("pages", []):
            page_id = store.add_page(database_row["id"], page["title"], page.get("properties", {}))
            refs_dict[page["ref"]] = page_id

    crm_dict = fixtures_dict.get("crm", {})
    for company in crm_dict.get("companies", []):
        refs_dict[company["ref"]] = store.add_company(company["name"], company.get("domain"))
    for person in crm_dict.get("people", []):
        person_dict = {**person, "company_id": refs_dict.get(person.get("company", ""))}
        refs_dict[person["ref"]] = store.add_person(person_dict)
    for opportunity in crm_dict.get("opportunities", []):
        opportunity_dict = {
            **opportunity,
            "company_id": refs_dict.get(opportunity.get("company", "")),
            "point_of_contact_id": refs_dict.get(opportunity.get("point_of_contact", "")),
        }
        refs_dict[opportunity["ref"]] = store.add_opportunity(opportunity_dict)
    return refs_dict


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
