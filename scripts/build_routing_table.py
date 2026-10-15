"""Generate configs/routing_table.yaml from results/e1_step_success.json."""

# ============= Local =============
from roiconsole.common.config import config_path
from roiconsole.common.results import read_results
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.routing_table import build_routing_table
from roiconsole.gateway.pricing import PriceTable
from roiconsole.gateway.service import missing_price_models

# ============= Constants =============
E1_NAME = "e1_step_success"


# ============= Entry Point =============


def main() -> None:
    """Write the routing table and refuse when any candidate lacks a price."""
    e1_results = read_results(E1_NAME)
    if e1_results is None:
        raise SystemExit("results/e1_step_success.json not found; run make eval-steps first")
    prices = PriceTable.load()
    table = build_routing_table(e1_results, get_settings().results_dir / f"{E1_NAME}.json", prices)
    missing_list = missing_price_models(table, prices)
    if missing_list:
        raise SystemExit(f"routing table models without a price: {missing_list}")
    out = config_path("routing_table.yaml")
    table.write(out)
    print(f"wrote {out} with step classes {table.step_classes()}")


if __name__ == "__main__":
    main()
