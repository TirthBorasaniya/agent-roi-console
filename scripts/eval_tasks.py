"""E2: run held-out tasks on every path and model configuration; write results/e2_tasks.json."""

# ============= Standard Library =============
import argparse
import asyncio

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.logging import configure_logging
from roiconsole.common.results import write_results
from roiconsole.evaluation.e2_tasks import run_e2
from roiconsole.runner.session import GATEWAY_MODES, MCP_MODES, runner_session

# ============= Entry Point =============


async def run(args: argparse.Namespace) -> None:
    """Run E2 (resuming from its checkpoint) and write the results file."""
    async with runner_session(args.gateway, args.mcp) as runner:
        metrics_dict, record_list, config_file = await run_e2(
            runner, o_run_from_scratch=args.o_run_from_scratch
        )
    selfhosted = load_yaml(config_path("selfhosted.yaml"))["model"]["description"]
    notes = (
        f"connector_mode={runner.backends.mode}; gateway caching off; "
        f"self-hosted model served: {selfhosted}; "
        f"skipped (unpriced): {sorted(metrics_dict['model_configs_skipped'])}"
    )
    path = write_results(
        "e2_tasks", "e2_tasks", config_file, metrics_dict, notes, {"records": record_list}
    )
    print(f"wrote {path}")


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", choices=GATEWAY_MODES, default="inprocess")
    parser.add_argument("--mcp", choices=MCP_MODES, default="stdio")
    parser.add_argument("--o-run-from-scratch", action="store_true")
    configure_logging("eval-tasks")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
