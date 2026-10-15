"""Reset the workspace (sandbox store, or live Slack, Notion, and Twenty) to one task's fixtures."""

# ============= Standard Library =============
import argparse
import json

# ============= Local =============
from roiconsole.mcp_servers.backends.factory import build_backends
from roiconsole.sandbox.fixtures import resolve_inputs
from roiconsole.workflows.registry import load_tasks

# ============= Entry Point =============


def main() -> None:
    """Reset the workspace of the configured connector mode and print refs and inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="task id, e.g. slack_digest_001")
    args = parser.parse_args()
    task = load_tasks()[args.task]
    backends = build_backends()
    refs_dict = backends.reset(task.fixtures)
    summary_dict = {
        "mode": backends.mode,
        "refs": refs_dict,
        "inputs": resolve_inputs(task, refs_dict),
    }
    print(json.dumps(summary_dict, indent=2))


if __name__ == "__main__":
    main()
