"""Build connector backends for a mode, and reset or snapshot the whole workspace."""

# ============= Standard Library =============
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.settings import Settings, get_settings
from roiconsole.mcp_servers.backends.crm import CrmBackend, SimulatedCrmBackend
from roiconsole.mcp_servers.backends.notion import (
    LiveNotionBackend,
    NotionBackend,
    SandboxNotionBackend,
)
from roiconsole.mcp_servers.backends.slack import (
    LiveSlackBackend,
    SandboxSlackBackend,
    SlackBackend,
)
from roiconsole.mcp_servers.common import IdempotencyStore, WriteThrottle
from roiconsole.sandbox.fixtures import database_schema, load_connectors_config
from roiconsole.sandbox.store import SandboxStore
from roiconsole.validators.state import EndState

# ============= Constants =============
LIVE_REFS_FILE = "live_refs.json"
IDEMPOTENCY_FILE = "mcp_idempotency.db"


# ============= Workspace =============


@dataclass
class ConnectorBackends:
    """
    The three connector backends of one mode, with workspace-level reset and snapshot.

    Attributes
    ----------
    mode : str
        live, dryrun, or demo.
    slack, notion, crm : backends
        Live API backends in live mode, sandbox backends otherwise.
    idempotency : IdempotencyStore
        Shared key store for write tools.
    sandbox_store : SandboxStore or None
        Present in dryrun and demo modes.
    """

    mode: str
    slack: SlackBackend
    notion: NotionBackend
    crm: CrmBackend
    idempotency: IdempotencyStore
    sandbox_store: SandboxStore | None = None

    def reset(self, fixtures_dict: dict[str, Any]) -> dict[str, str]:
        """
        Restore the workspace to one task's fixtures.

        Parameters
        ----------
        fixtures_dict : dict
            The task's fixtures with slack, notion, and crm sections.

        Returns
        -------
        refs_dict : dict
            Fixture ref to concrete id.
        """
        connectors_dict = load_connectors_config()
        if self.sandbox_store is not None:
            self.sandbox_store.reset()
            for channel in connectors_dict["slack"]["channel_allowlist"]:
                self.sandbox_store.ensure_channel(channel)
            for name in connectors_dict["notion"]["databases"]:
                title_property, schema_dict = database_schema(name)
                self.sandbox_store.ensure_database(name, title_property, schema_dict)
            # sandbox runs start clean; live keys persist because live writes persist
            self.idempotency.clear()
        refs_dict: dict[str, str] = {}
        for thread in fixtures_dict.get("slack", {}).get("threads", []):
            refs_dict[thread["ref"]] = self.slack.post_fixture_thread(
                thread["channel"], thread["messages"]
            )
        pages_by_database = {
            d["name"]: d.get("pages", [])
            for d in fixtures_dict.get("notion", {}).get("databases", [])
        }
        for name in connectors_dict["notion"]["databases"]:
            refs_dict.update(self.notion.reset_database(name, pages_by_database.get(name, [])))
        refs_dict.update(self.crm.reset(fixtures_dict.get("crm", {})))
        return refs_dict

    def snapshot(self, since: float) -> EndState:
        """Read everything created since `since` into an EndState for validators."""
        return EndState(
            slack_messages=self.slack.messages_since(since),
            notion_pages=self.notion.pages_since(since),
            crm_activities=self.crm.activities_since(since),
        )


# ============= Builders =============


def load_live_refs(settings: Settings) -> dict[str, Any]:
    """Load data/live_refs.json written by the seed scripts."""
    path = settings.data_dir / LIVE_REFS_FILE
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing; run scripts/seed_notion.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def write_live_refs(settings: Settings, refs_dict: dict[str, Any]) -> Path:
    """Merge keys into data/live_refs.json."""
    path = settings.data_dir / LIVE_REFS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    current_dict = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    current_dict.update(refs_dict)
    path.write_text(json.dumps(current_dict, indent=2) + "\n", encoding="utf-8")
    return path


def build_sandbox_backends(mode: str, store: SandboxStore, idempotency: IdempotencyStore):
    """Return sandbox Slack, Notion, and simulated CRM backends."""
    connectors_dict = load_connectors_config()
    return ConnectorBackends(
        mode=mode,
        slack=SandboxSlackBackend(store, connectors_dict["slack"]["channel_allowlist"]),
        notion=SandboxNotionBackend(store, list(connectors_dict["notion"]["databases"])),
        crm=SimulatedCrmBackend(store, connectors_dict["crm"]["open_stages"]),
        idempotency=idempotency,
        sandbox_store=store,
    )


def build_live_backends(settings: Settings, idempotency: IdempotencyStore) -> ConnectorBackends:
    """Return live Slack, Notion, and Twenty backends."""
    # imported here so sandbox-only processes never import the Twenty client
    from roiconsole.mcp_servers.backends.twenty import TwentyClient, TwentyCrmBackend

    connectors_dict = load_connectors_config()
    live_refs_dict = load_live_refs(settings)
    schemas_dict = {name: database_schema(name) for name in connectors_dict["notion"]["databases"]}
    slack = LiveSlackBackend(
        settings.slack_bot_token,
        connectors_dict["slack"]["channel_allowlist"],
        WriteThrottle(connectors_dict["slack"]["write_min_interval_s"]),
    )
    notion = LiveNotionBackend(
        settings.notion_api_key,
        live_refs_dict["notion"],
        schemas_dict,
        WriteThrottle(connectors_dict["notion"]["write_min_interval_s"]),
    )
    crm = TwentyCrmBackend(
        TwentyClient(settings.twenty_api_url, settings.twenty_api_key),
        connectors_dict["crm"]["open_stages"],
        connectors_dict["crm"]["sandbox_marker"],
    )
    return ConnectorBackends("live", slack, notion, crm, idempotency)


def build_backends(settings: Settings | None = None, mode: str | None = None) -> ConnectorBackends:
    """
    Build the backends for the configured or given mode.

    Parameters
    ----------
    settings : Settings or None
        Process settings; defaults to the global settings.
    mode : str or None
        Override of the connector mode.

    Returns
    -------
    backends : ConnectorBackends
        Ready-to-use backends.
    """
    settings = settings or get_settings()
    mode = mode or settings.resolved_connector_mode
    idempotency = IdempotencyStore(settings.data_dir / IDEMPOTENCY_FILE)
    if mode == "live":
        return build_live_backends(settings, idempotency)
    store = SandboxStore(settings.resolved_sandbox_db_path)
    return build_sandbox_backends(mode, store, idempotency)
