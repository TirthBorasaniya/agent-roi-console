"""Run one task per workflow on live Slack, Notion, and Twenty; write results/live_tasks.json."""

# ============= Standard Library =============
import argparse
import asyncio
import os

# ============= Local =============
from roiconsole.common.logging import configure_logging
from roiconsole.common.results import write_results
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.stats import rate_summary
from roiconsole.runner.session import GATEWAY_MODES, runner_session
from roiconsole.workflows.registry import load_workflows, tasks_for

# ============= Entry Point =============


async def run(args: argparse.Namespace) -> None:
    """Run the first dev task of each workflow on the given path and model in live mode."""
    os.environ["ROI_CONNECTOR_MODE"] = "live"
    get_settings.cache_clear()
    record_list = []
    async with runner_session(args.gateway, "inprocess") as runner:
        if runner.backends.mode != "live":
            raise SystemExit("live credentials are required")
        for workflow_id in load_workflows():
            task = tasks_for("dev", workflow_id)[0]
            outcome = await runner.run(task, args.path, args.model, trigger="live")
            record_list.append(outcome.to_dict())
            print(
                f"{workflow_id}: {task.task_id} success={outcome.success} "
                f"({outcome.failure_reason})"
            )
    metrics_dict = {
        "path": args.path,
        "model": args.model,
        "connectors": {
            "slack": "Slack Web API",
            "notion": "Notion API 2025-09-03",
            "crm": "self-hosted Twenty GraphQL",
        },
        "success": rate_summary(sum(r["success"] for r in record_list), len(record_list)),
        "per_workflow": {
            r["workflow_id"]: {
                "task_id": r["task_id"],
                "success": r["success"],
                "run_id": r["run_id"],
            }
            for r in record_list
        },
    }
    path = write_results(
        "live_tasks",
        "live_tasks",
        None,
        metrics_dict,
        "live connector run",
        {"records": record_list},
    )
    print(f"wrote {path}")


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", choices=GATEWAY_MODES, default="inprocess")
    parser.add_argument("--path", default="pipeline")
    parser.add_argument("--model", default="auto")
    configure_logging("live-tasks")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
