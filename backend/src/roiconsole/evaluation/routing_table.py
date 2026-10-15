"""Build configs/routing_table.yaml from the E1 results file."""

# ============= Standard Library =============
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import relative_to_repo, sha256_file
from roiconsole.gateway.pricing import PriceTable
from roiconsole.gateway.router import Candidate, RoutingTable

# ============= Builder =============


def build_routing_table(
    e1_results: dict[str, Any], e1_path: Path, prices: PriceTable
) -> RoutingTable:
    """
    Turn E1 step-class summaries into a routing table.

    For each step class, every priced model measured in E1 becomes a candidate
    with its measured step success, trial count, and mean cost per call;
    candidates are sorted by mean cost. The table records the evaluation date
    and the source file hash, so a stale table is detectable (SPEC 12).
    """
    candidates_dict: dict[str, list[Candidate]] = {}
    for step_class, models_dict in e1_results["metrics"]["step_classes"].items():
        candidate_list = [
            Candidate(
                model=model,
                step_success=float(entry["success"]["rate"] or 0.0),
                n_trials=int(entry["success"]["trials"]),
                mean_cost_usd=float(entry["mean_cost_usd"] or 0.0),
            )
            for model, entry in models_dict.items()
            if prices.has(model)
        ]
        candidates_dict[step_class] = sorted(candidate_list, key=lambda c: c.mean_cost_usd)
    metadata_dict = {
        "version": 1,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evaluation_date": e1_results["timestamp_utc"][:10],
        "source": relative_to_repo(e1_path),
        "source_sha256": sha256_file(e1_path),
        "source_run_id": e1_results["run_id"],
    }
    return RoutingTable(candidates_dict, metadata_dict)
