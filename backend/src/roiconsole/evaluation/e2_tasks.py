"""E2: held-out task success, cost per successful task, and latency per path and model."""

# ============= Standard Library =============
import logging
from collections import Counter
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.settings import REPO_ROOT
from roiconsole.evaluation.checkpoint import Checkpoint
from roiconsole.evaluation.stats import mean, percentile, rate_summary
from roiconsole.gateway.pricing import PriceTable
from roiconsole.runner.runner import TaskRunner
from roiconsole.workflows.registry import tasks_for

# ============= Constants =============
logger = logging.getLogger(__name__)
E2_CONFIG = "eval/e2.yaml"
KEY_FIELDS = ("task_id", "path", "model_config", "repeat")
ROUTED = "auto"


# ============= Aggregation =============


def summarize_group(record_list: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Summarize runs of one group.

    Returns
    -------
    summary : dict
        success rate with Wilson interval, total and mean cost, cost per
        successful task, latency mean and percentiles, and failure reasons.
    """
    n_success = sum(bool(r["success"]) for r in record_list)
    total_cost = sum(r["llm_cost_usd"] + r["tool_cost_usd"] for r in record_list)
    latency_list = [r["latency_s"] for r in record_list]
    return {
        "success": rate_summary(n_success, len(record_list)),
        "total_cost_usd": total_cost,
        "mean_cost_per_task_usd": total_cost / len(record_list) if record_list else None,
        "cost_per_successful_task_usd": total_cost / n_success if n_success else None,
        "latency_mean_s": mean(latency_list),
        "latency_p50_s": percentile(latency_list, 0.5),
        "latency_p95_s": percentile(latency_list, 0.95),
        "failure_reasons": dict(
            Counter(r["failure_reason"] for r in record_list if not r["success"])
        ),
    }


def aggregate(record_list: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate E2 records by path and configuration, overall and per workflow.

    Also reports the share of routed calls each model received and, per path,
    the routed configuration's cost per success relative to each single model.
    """
    by_config: dict[str, dict[str, Any]] = {}
    by_workflow: dict[str, dict[str, dict[str, Any]]] = {}
    for path in sorted({r["path"] for r in record_list}):
        by_config[path] = {}
        for config in sorted({r["model_config"] for r in record_list if r["path"] == path}):
            subset = [r for r in record_list if r["path"] == path and r["model_config"] == config]
            by_config[path][config] = summarize_group(subset)
            for workflow_id in sorted({r["workflow_id"] for r in subset}):
                by_workflow.setdefault(workflow_id, {}).setdefault(path, {})[config] = (
                    summarize_group([r for r in subset if r["workflow_id"] == workflow_id])
                )
    share_counter: Counter = Counter()
    for record in record_list:
        if record["model_config"] == ROUTED:
            share_counter.update(record["models_used"])
    total_calls = sum(share_counter.values())
    routed_share = (
        {m: c / total_calls for m, c in share_counter.most_common()} if total_calls else {}
    )
    comparison_dict: dict[str, dict[str, Any]] = {}
    for path, configs_dict in by_config.items():
        routed = configs_dict.get(ROUTED)
        if not routed:
            continue
        comparison_dict[path] = {
            config: {
                "success_delta": (routed["success"]["rate"] or 0)
                - (summary["success"]["rate"] or 0),
                "cost_per_success_ratio": (
                    routed["cost_per_successful_task_usd"] / summary["cost_per_successful_task_usd"]
                    if routed["cost_per_successful_task_usd"]
                    and summary["cost_per_successful_task_usd"]
                    else None
                ),
            }
            for config, summary in configs_dict.items()
            if config != ROUTED
        }
    return {
        "by_path_and_config": by_config,
        "by_workflow": by_workflow,
        "routed_model_share": routed_share,
        "routed_vs_single_model": comparison_dict,
    }


# ============= Experiment =============


async def run_e2(
    runner: TaskRunner, config_file: Path | None = None, o_run_from_scratch: bool = False
):
    """
    Run every held-out task on every path with every model configuration.

    Progress is checkpointed after each run so an overnight evaluation resumes
    where it stopped. Unpriced single-model configurations are skipped and listed.

    Returns
    -------
    metrics : dict
        Aggregates from `aggregate`, plus counts and skipped configurations.
    records : list of dict
        Every run outcome (consumed by E3).
    config_file : Path
        Config used.
    """
    config_file = config_file or config_path(E2_CONFIG)
    config_dict = load_yaml(config_file)
    prices = PriceTable.load()
    config_list = [c for c in config_dict["model_configs"] if c == ROUTED or prices.has(c)]
    skipped_dict = {
        c: prices.unpriced_dict.get(c, "no price")
        for c in config_dict["model_configs"]
        if c not in config_list
    }
    checkpoint = Checkpoint(REPO_ROOT / config_dict["checkpoint"], KEY_FIELDS, o_run_from_scratch)
    task_list = tasks_for(config_dict["split"])
    for repeat in range(config_dict["repeats"]):
        for task in task_list:
            for path in config_dict["paths"]:
                for model_config in config_list:
                    key = (task.task_id, path, model_config, str(repeat))
                    if checkpoint.done(key):
                        continue
                    outcome = await runner.run(task, path, model_config, trigger="eval_e2")
                    checkpoint.add({**outcome.to_dict(), "repeat": repeat})
                    logger.info(
                        "e2 record", extra={"key": "/".join(key), "success": outcome.success}
                    )
    record_list = checkpoint.records()
    metrics_dict = {
        "split": config_dict["split"],
        "n_tasks": len(task_list),
        "n_runs": len(record_list),
        "paths": config_dict["paths"],
        "model_configs": config_list,
        "model_configs_skipped": skipped_dict,
        **aggregate(record_list),
    }
    return metrics_dict, record_list, config_file
