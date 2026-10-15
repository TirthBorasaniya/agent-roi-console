"""Restore Twenty to one task's CRM fixtures, deleting only sandbox-marked records."""

# ============= Standard Library =============
import argparse
import json

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.mcp_servers.backends.twenty import TwentyClient, TwentyCrmBackend
from roiconsole.sandbox.fixtures import load_connectors_config
from roiconsole.workflows.registry import load_tasks

# ============= Entry Point =============


def main() -> None:
    """Delete sandbox records, then load the task's fixtures (or none with --empty)."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task")
    group.add_argument("--empty", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    crm_dict = load_connectors_config()["crm"]
    backend = TwentyCrmBackend(
        TwentyClient(settings.twenty_api_url, settings.twenty_api_key),
        crm_dict["open_stages"],
        crm_dict["sandbox_marker"],
    )
    if args.empty:
        print(json.dumps({"deleted": backend.delete_sandbox_records()}, indent=2))
        return
    refs_dict = backend.reset(load_tasks()[args.task].fixtures.get("crm", {}))
    print(json.dumps({"task": args.task, "refs": refs_dict}, indent=2))


if __name__ == "__main__":
    main()
