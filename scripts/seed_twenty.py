"""Check the Twenty schema, then seed the sandbox companies, people, and opportunities."""

# ============= Standard Library =============
import argparse
import json

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.twenty import TwentyClient, TwentyCrmBackend
from roiconsole.mcp_servers.backends.twenty_schema import check_schema
from roiconsole.sandbox.fixtures import load_connectors_config
from roiconsole.workflows.registry import load_tasks

# ============= Constants =============
DEFAULT_TASK = "crm_pipeline_001"


# ============= Entry Point =============


def main() -> None:
    """Verify the GraphQL schema and load one task's CRM fixtures as the starting state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", default=DEFAULT_TASK)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.twenty_api_key:
        raise SystemExit("TWENTY_API_KEY must be set (Twenty Settings, API and Webhooks)")
    client = TwentyClient(settings.twenty_api_url, settings.twenty_api_key)
    missing_list = check_schema(client)
    if missing_list:
        raise SystemExit(f"Twenty schema is missing fields the connector uses: {missing_list}")
    crm_dict = load_connectors_config()["crm"]
    backend = TwentyCrmBackend(client, crm_dict["open_stages"], crm_dict["sandbox_marker"])
    refs_dict = backend.reset(load_tasks()[args.task].fixtures.get("crm", {}))
    print(json.dumps({"seeded_task": args.task, "records": len(refs_dict)}, indent=2))


if __name__ == "__main__":
    main()
