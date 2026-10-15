"""E4: run the gateway fault, budget, and cache experiment and write results/e4_gateway.json."""

# ============= Standard Library =============
import argparse
import asyncio
from pathlib import Path

# ============= Local =============
from roiconsole.common.logging import configure_logging
from roiconsole.common.results import write_results
from roiconsole.common.settings import REPO_ROOT
from roiconsole.evaluation.e4_gateway import run_e4

# ============= Entry Point =============


def main() -> None:
    """Run E4 with configs/eval/e4.yaml (or --config) and write the results file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    configure_logging("eval-gateway")
    metrics_dict, config_file = asyncio.run(run_e4(REPO_ROOT / ".tmp" / "e4", args.config))
    notes = (
        f"provider_mode={metrics_dict['provider_mode']}; faults injected on the primary model "
        "only; budget enforcement verified on fake providers with exact prices"
    )
    path = write_results("e4_gateway", "e4_gateway", config_file, metrics_dict, notes=notes)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
