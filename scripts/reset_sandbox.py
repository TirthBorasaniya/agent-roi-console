"""Reset the sandbox workspace to one task's fixtures and print the resolved refs."""

# ============= Standard Library =============
import argparse
import json

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.sandbox.fixtures import load_sandbox_fixtures, resolve_inputs
from roiconsole.sandbox.store import SandboxStore
from roiconsole.workflows.registry import load_tasks


# ============= Entry Point =============


def main() -> None:
    """Load a task's fixtures into the SQLite sandbox store."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="task id, e.g. slack_digest_001")
    args = parser.parse_args()
    task = load_tasks()[args.task]
    store = SandboxStore(get_settings().resolved_sandbox_db_path)
    refs_dict = load_sandbox_fixtures(store, task.fixtures)
    print(json.dumps({"refs": refs_dict, "inputs": resolve_inputs(task, refs_dict)}, indent=2))


if __name__ == "__main__":
    main()
