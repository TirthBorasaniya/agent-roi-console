"""E3: ROI, ROI ratio, break-even, and sensitivity per workflow from E2 run records."""

# ============= Standard Library =============
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.results import read_results
from roiconsole.roi.compute import Economics, sensitivity_grid, summarize_runs
from roiconsole.workflows.registry import load_workflows

# ============= Constants =============
E3_CONFIG = "eval/e3.yaml"


# ============= Experiment =============


def run_e3(config_file: Path | None = None) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """
    Compute ROI for every workflow, path, and model configuration in E2.

    Returns
    -------
    metrics : dict
        Per workflow: assumptions, per path and configuration ROI summaries with
        measured versus break-even success, and the sensitivity grid per path.
    config_file : Path
        Config used.
    source : dict
        run_id and timestamp of the E2 results consumed.
    """
    config_file = config_file or config_path(E3_CONFIG)
    config_dict = load_yaml(config_file)
    e2 = read_results(config_dict["source_results"])
    if e2 is None:
        raise FileNotFoundError("results/e2_tasks.json not found; run make eval-tasks first")
    record_list = e2["records"]
    workflows_dict: dict[str, Any] = {}
    for workflow_id, workflow in load_workflows().items():
        economics = Economics.from_workflow(workflow)
        workflow_records = [r for r in record_list if r["workflow_id"] == workflow_id]
        by_path: dict[str, Any] = {}
        sensitivity_by_path: dict[str, Any] = {}
        for path in sorted({r["path"] for r in workflow_records}):
            path_records = [r for r in workflow_records if r["path"] == path]
            by_path[path] = {}
            for model_config in sorted({r["model_config"] for r in path_records}):
                subset = [r for r in path_records if r["model_config"] == model_config]
                summary = summarize_runs(subset, economics)
                summary["o_above_break_even"] = (
                    summary["break_even_success_rate"] is not None
                    and summary["success"]["rate"] >= summary["break_even_success_rate"]
                )
                by_path[path][model_config] = summary
            sensitivity_subset = [
                r
                for r in path_records
                if r["model_config"] == config_dict["sensitivity_model_config"]
            ]
            if sensitivity_subset:
                sensitivity_by_path[path] = sensitivity_grid(sensitivity_subset, economics)
        workflows_dict[workflow_id] = {
            "assumptions": {
                "minutes_per_task": economics.minutes_per_task,
                "minutes_source": workflow.human_baseline.source,
                "o_timed": workflow.human_baseline.o_timed,
                "labor_rate_usd_per_hour": economics.labor_rate_usd_per_hour,
                "review_minutes_per_task": economics.review_minutes_per_task,
                "fix_minutes_per_failure": economics.fix_minutes_per_failure,
                "tool_cost_usd_per_task": economics.tool_cost_usd_per_task,
            },
            "by_path_and_config": by_path,
            "sensitivity": {
                "model_config": config_dict["sensitivity_model_config"],
                "by_path": sensitivity_by_path,
            },
        }
    return (
        {"workflows": workflows_dict},
        config_file,
        {"e2_run_id": e2["run_id"], "e2_timestamp_utc": e2["timestamp_utc"]},
    )
