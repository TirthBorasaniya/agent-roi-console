"""E3: ROI, sensitivity, and break-even from E2 runs, written to results/e3_roi.json."""

# ============= Local =============
from roiconsole.common.results import write_results
from roiconsole.evaluation.e3_roi import run_e3

# ============= Entry Point =============


def main() -> None:
    """Run E3."""
    metrics_dict, config_file, source_dict = run_e3()
    notes = "human baselines are labeled assumptions unless o_timed is true; see docs/baselines.md"
    path = write_results(
        "e3_roi", "e3_roi", config_file, metrics_dict, notes, {"source": source_dict}
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
