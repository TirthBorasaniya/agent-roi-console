"""E1: measure step success per step class per model and write results/e1_step_success.json."""

# ============= Standard Library =============
import argparse
import asyncio

# ============= Local =============
from roiconsole.common.logging import configure_logging
from roiconsole.common.results import write_results
from roiconsole.evaluation.e1_steps import run_e1
from roiconsole.runner.session import GATEWAY_MODES, MCP_MODES, runner_session

# ============= Entry Point =============


async def run(args: argparse.Namespace) -> None:
    """Run E1 and write the results file."""
    async with runner_session(args.gateway, args.mcp) as runner:
        metrics_dict, record_list, config_file = await run_e1(
            runner, o_run_from_scratch=args.o_run_from_scratch
        )
    skipped_list = sorted(metrics_dict["models_skipped"])
    notes = f"connector_mode={runner.backends.mode}; skipped (unpriced): {skipped_list}"
    path = write_results(
        "e1_step_success",
        "e1_step_success",
        config_file,
        metrics_dict,
        notes,
        {"records": record_list},
    )
    print(f"wrote {path}; now run scripts/build_routing_table.py")


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", choices=GATEWAY_MODES, default="inprocess")
    parser.add_argument("--mcp", choices=MCP_MODES, default="stdio")
    parser.add_argument("--o-run-from-scratch", action="store_true")
    configure_logging("eval-steps")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
