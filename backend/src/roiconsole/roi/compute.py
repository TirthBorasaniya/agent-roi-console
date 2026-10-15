"""Per-task and aggregate ROI, break-even points, and the sensitivity grid."""

# ============= Standard Library =============
from dataclasses import dataclass, replace
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.evaluation.stats import rate_summary
from roiconsole.workflows.schema import WorkflowDefinition

# ============= Constants =============
MINUTES_PER_HOUR = 60.0
ROI_CONFIG = "roi.yaml"


# ============= Types =============


@dataclass(frozen=True)
class Economics:
    """
    Human-time assumptions of one workflow.

    Attributes
    ----------
    minutes_per_task : float
        Human baseline minutes for one task.
    labor_rate_usd_per_hour : float
        Fully loaded labor rate.
    review_minutes_per_task : float
        Minutes to check each agent output.
    fix_minutes_per_failure : float
        Minutes to repair a failed task.
    tool_cost_usd_per_task : float
        Connector cost per task (0 unless configured).
    """

    minutes_per_task: float
    labor_rate_usd_per_hour: float
    review_minutes_per_task: float
    fix_minutes_per_failure: float
    tool_cost_usd_per_task: float = 0.0

    @classmethod
    def from_workflow(cls, workflow: WorkflowDefinition) -> "Economics":
        """Read the assumptions from a workflow definition."""
        return cls(
            workflow.human_baseline.minutes_per_task,
            workflow.labor_rate_usd_per_hour,
            workflow.review_minutes_per_task,
            workflow.fix_minutes_per_failure,
            workflow.tool_cost_usd_per_task,
        )


@dataclass(frozen=True)
class TaskRoi:
    """ROI terms of one task."""

    value_usd: float
    human_cost_usd: float
    machine_cost_usd: float
    cost_usd: float
    net_usd: float


# ============= Per Task =============


def task_roi(
    success: float, llm_cost_usd: float, economics: Economics, tool_cost_usd: float | None = None
) -> TaskRoi:
    """
    Compute SPEC 6 per-task ROI.

    value = success * minutes_per_task / 60 * labor_rate
    human_cost = (review_minutes + (1 - success) * fix_minutes) / 60 * labor_rate
    cost = llm_cost + tool_cost + human_cost
    net = value - cost

    Parameters
    ----------
    success : float
        1.0 for a successful task, 0.0 for a failure (a rate is accepted for expectations).
    llm_cost_usd : float
        Measured model cost of the run.
    economics : Economics
        Human-time assumptions.
    tool_cost_usd : float or None
        Override of the configured tool cost.

    Returns
    -------
    roi : TaskRoi
        Value, cost terms, and net.
    """
    if not 0.0 <= success <= 1.0:
        raise ValueError("success must be in [0, 1]")
    rate_per_minute = economics.labor_rate_usd_per_hour / MINUTES_PER_HOUR
    tool_cost = economics.tool_cost_usd_per_task if tool_cost_usd is None else tool_cost_usd
    value = success * economics.minutes_per_task * rate_per_minute
    human_cost = (
        economics.review_minutes_per_task + (1.0 - success) * economics.fix_minutes_per_failure
    ) * rate_per_minute
    machine_cost = llm_cost_usd + tool_cost
    cost = machine_cost + human_cost
    return TaskRoi(value, human_cost, machine_cost, cost, value - cost)


# ============= Aggregates =============


def break_even_success_rate(mean_machine_cost_usd: float, economics: Economics) -> float | None:
    """
    Success rate at which expected net ROI per task is zero.

    Solving s*M*r = c + (R + (1 - s)*F)*r for s, with r the labor rate per
    minute and c the mean machine cost per task, gives
    s = (c + (R + F)*r) / ((M + F)*r). Returns None when s exceeds 1, i.e. no
    success rate breaks even.
    """
    rate_per_minute = economics.labor_rate_usd_per_hour / MINUTES_PER_HOUR
    denominator = (economics.minutes_per_task + economics.fix_minutes_per_failure) * rate_per_minute
    if denominator <= 0:
        return None
    rate = (
        mean_machine_cost_usd
        + (economics.review_minutes_per_task + economics.fix_minutes_per_failure) * rate_per_minute
    ) / denominator
    return rate if rate <= 1.0 else None


def summarize_runs(run_list: list[dict[str, Any]], economics: Economics) -> dict[str, Any]:
    """
    ROI over a set of runs.

    Parameters
    ----------
    run_list : list of dict
        Each with success (bool), llm_cost_usd, and optional tool_cost_usd.
    economics : Economics
        Assumptions of the workflow.

    Returns
    -------
    summary : dict
        n_runs, success rate with Wilson interval, value, cost, net ROI
        (total and per task), ROI ratio value / cost, cost per successful
        task (machine and total), break-even success rate, and break-even
        minutes per task.
    """
    if not run_list:
        return {"n_runs": 0}
    roi_list = [
        task_roi(
            float(bool(r["success"])), float(r["llm_cost_usd"]), economics, r.get("tool_cost_usd")
        )
        for r in run_list
    ]
    n_success = sum(bool(r["success"]) for r in run_list)
    value = sum(t.value_usd for t in roi_list)
    cost = sum(t.cost_usd for t in roi_list)
    machine_cost = sum(t.machine_cost_usd for t in roi_list)
    total_cost_per_success = cost / n_success if n_success else None
    return {
        "n_runs": len(run_list),
        "success": rate_summary(n_success, len(run_list)),
        "value_usd": value,
        "human_cost_usd": sum(t.human_cost_usd for t in roi_list),
        "machine_cost_usd": machine_cost,
        "cost_usd": cost,
        "net_roi_usd": value - cost,
        "net_roi_per_task_usd": (value - cost) / len(run_list),
        "roi_ratio": value / cost if cost > 0 else None,
        "machine_cost_per_success_usd": machine_cost / n_success if n_success else None,
        "cost_per_success_usd": total_cost_per_success,
        "break_even_success_rate": break_even_success_rate(machine_cost / len(run_list), economics),
        "break_even_minutes_per_task": (
            total_cost_per_success * MINUTES_PER_HOUR / economics.labor_rate_usd_per_hour
            if total_cost_per_success is not None
            else None
        ),
    }


def sensitivity_grid(
    run_list: list[dict[str, Any]], economics: Economics, config_dict: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """
    Recompute ROI across minutes-per-task multipliers and labor rates.

    Returns
    -------
    grid : list of dict
        One entry per (multiplier, rate): minutes_per_task, labor rate, net ROI
        per task, ROI ratio, break-even minutes per task, and break-even success rate.
    """
    config_dict = config_dict or load_yaml(config_path(ROI_CONFIG))["sensitivity"]
    grid_list = []
    for multiplier in config_dict["minutes_multipliers"]:
        for rate in config_dict["labor_rates_usd_per_hour"]:
            scenario = replace(
                economics,
                minutes_per_task=economics.minutes_per_task * multiplier,
                labor_rate_usd_per_hour=float(rate),
            )
            summary = summarize_runs(run_list, scenario)
            grid_list.append(
                {
                    "minutes_multiplier": multiplier,
                    "minutes_per_task": scenario.minutes_per_task,
                    "labor_rate_usd_per_hour": float(rate),
                    "net_roi_per_task_usd": summary.get("net_roi_per_task_usd"),
                    "roi_ratio": summary.get("roi_ratio"),
                    "break_even_minutes_per_task": summary.get("break_even_minutes_per_task"),
                    "break_even_success_rate": summary.get("break_even_success_rate"),
                }
            )
    return grid_list
