"""Read models for the API, computed from the console database and configs."""

# ============= Standard Library =============
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from roiconsole.common.config import relative_to_repo, sha256_file

# ============= Local =============
from roiconsole.common.db import rows_to_dicts, session, utc_now
from roiconsole.gateway.budgets import BudgetConfig
from roiconsole.guard.service import load_state
from roiconsole.roi.compute import Economics, sensitivity_grid, summarize_runs
from roiconsole.workflows.registry import load_workflows, workflow_path

# ============= Constants =============
# E1 step probes are not workflow runs; every other trigger counts toward ROI views
EXCLUDED_TRIGGERS = ("eval_e1",)
DEFAULT_WINDOW_HOURS = 24 * 30


# ============= Workflows =============


def sync_workflows() -> None:
    """Upsert workflow rows from configs/workflows and create default guard state."""
    with session() as conn:
        for workflow_id, workflow in load_workflows().items():
            path = workflow_path(workflow_id)
            conn.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET "
                "name = excluded.name, description = excluded.description, "
                "value_category = excluded.value_category, "
                "definition_path = excluded.definition_path, "
                "definition_sha256 = excluded.definition_sha256, updated_at = excluded.updated_at",
                (
                    workflow_id,
                    workflow.name,
                    workflow.description,
                    workflow.value_category,
                    relative_to_repo(path),
                    sha256_file(path),
                    utc_now(),
                ),
            )
    for workflow_id, workflow in load_workflows().items():
        load_state(workflow_id, workflow.default_path)


def workflow_view(workflow_id: str) -> dict[str, Any]:
    """Return a workflow's definition summary, assumptions, and guard state."""
    workflow = load_workflows()[workflow_id]
    state = load_state(workflow_id, workflow.default_path)
    return {
        "workflow_id": workflow_id,
        "name": workflow.name,
        "description": workflow.description,
        "value_category": workflow.value_category,
        "default_path": workflow.default_path,
        "active_path": state.active_path,
        "guard_phase": state.phase,
        "o_paused": state.phase == "paused",
        "steps": [
            {"step_id": s.step_id, "type": s.type, "tool": s.tool, "step_class": s.step_class}
            for s in workflow.steps
        ],
        "assumptions": {
            "minutes_per_task": workflow.human_baseline.minutes_per_task,
            "minutes_source": workflow.human_baseline.source,
            "o_timed": workflow.human_baseline.o_timed,
            "labor_rate_usd_per_hour": workflow.labor_rate_usd_per_hour,
            "review_minutes_per_task": workflow.review_minutes_per_task,
            "fix_minutes_per_failure": workflow.fix_minutes_per_failure,
        },
    }


# ============= Runs =============


def completed_runs(
    workflow_id: str, window_hours: float = DEFAULT_WINDOW_HOURS
) -> list[dict[str, Any]]:
    """Return completed runs of a workflow in the window, oldest first."""
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    placeholders = ", ".join("?" for _ in EXCLUDED_TRIGGERS)
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE workflow_id = ? AND status IN ('COMPLETED', 'FAILED') "
            f"AND started_at >= ? AND trigger NOT IN ({placeholders}) ORDER BY started_at",
            (workflow_id, since, *EXCLUDED_TRIGGERS),
        ).fetchall()
    return rows_to_dicts(rows)


def roi_view(workflow_id: str, window_hours: float = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """ROI over the window, overall and per path."""
    economics = Economics.from_workflow(load_workflows()[workflow_id])
    run_list = completed_runs(workflow_id, window_hours)
    by_path = {
        path: summarize_runs([r for r in run_list if r["path"] == path], economics)
        for path in sorted({r["path"] for r in run_list})
    }
    return {
        "workflow_id": workflow_id,
        "window_hours": window_hours,
        "overall": summarize_runs(run_list, economics),
        "by_path": by_path,
    }


def sensitivity_view(
    workflow_id: str, window_hours: float = DEFAULT_WINDOW_HOURS
) -> dict[str, Any]:
    """Sensitivity grid over the window's runs."""
    economics = Economics.from_workflow(load_workflows()[workflow_id])
    run_list = completed_runs(workflow_id, window_hours)
    return {
        "workflow_id": workflow_id,
        "baseline_minutes_per_task": economics.minutes_per_task,
        "labor_rate_usd_per_hour": economics.labor_rate_usd_per_hour,
        "grid": sensitivity_grid(run_list, economics) if run_list else [],
    }


def list_runs(workflow_id: str | None, page: int, page_size: int) -> list[dict[str, Any]]:
    """Return runs newest first, optionally for one workflow."""
    query = "SELECT * FROM runs"
    params: list[Any] = []
    if workflow_id:
        query += " WHERE workflow_id = ?"
        params.append(workflow_id)
    query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
    params += [page_size, (page - 1) * page_size]
    with session() as conn:
        run_list = rows_to_dicts(conn.execute(query, params).fetchall())
    for run in run_list:
        run["validator_results"] = (
            json.loads(run["validator_results"]) if run["validator_results"] else []
        )
    return run_list


def run_detail(run_id: str) -> dict[str, Any] | None:
    """Return one run with steps, tool calls, and model calls."""
    with session() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        run = dict(row)
        run["validator_results"] = (
            json.loads(run["validator_results"]) if run["validator_results"] else []
        )
        steps = rows_to_dicts(
            conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        )
        for step in steps:
            step["detail"] = json.loads(step["detail"]) if step["detail"] else {}
        run["steps"] = steps
        run["tool_calls"] = rows_to_dicts(
            conn.execute(
                "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        )
        run["llm_calls"] = rows_to_dicts(
            conn.execute(
                "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        )
    return run


def model_mix(workflow_id: str, window_hours: float = DEFAULT_WINDOW_HOURS) -> dict[str, int]:
    """Count successful model calls per model for a workflow."""
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    with session() as conn:
        rows = conn.execute(
            "SELECT model, COUNT(*) AS n FROM llm_calls WHERE workflow_id = ? AND outcome = 'ok' "
            "AND created_at >= ? GROUP BY model ORDER BY n DESC",
            (workflow_id, since),
        ).fetchall()
    return {r["model"]: r["n"] for r in rows}


# ============= Overview and Gateway =============


def budget_burn(workflow_id: str) -> dict[str, Any]:
    """Today's model spend against the workflow's daily budget."""
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with session() as conn:
        spent = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_calls "
            "WHERE workflow_id = ? AND created_at >= ?",
            (workflow_id, day_start),
        ).fetchone()["s"]
    limit = BudgetConfig.load().for_workflow(workflow_id).per_workflow_daily_usd
    return {
        "spent_today_usd": spent,
        "daily_budget_usd": limit,
        "fraction": spent / limit if limit else None,
    }


def overview(window_hours: float = DEFAULT_WINDOW_HOURS) -> list[dict[str, Any]]:
    """Per workflow: net ROI, cost per success, success rate, budget burn, and guard state."""
    item_list = []
    for workflow_id in load_workflows():
        roi = roi_view(workflow_id, window_hours)["overall"]
        view = workflow_view(workflow_id)
        item_list.append(
            {
                "workflow_id": workflow_id,
                "name": view["name"],
                "active_path": view["active_path"],
                "guard_phase": view["guard_phase"],
                "n_runs": roi.get("n_runs", 0),
                "success": roi.get("success"),
                "net_roi_usd": roi.get("net_roi_usd"),
                "roi_ratio": roi.get("roi_ratio"),
                "cost_per_success_usd": roi.get("cost_per_success_usd"),
                "machine_cost_per_success_usd": roi.get("machine_cost_per_success_usd"),
                "break_even_success_rate": roi.get("break_even_success_rate"),
                "budget": budget_burn(workflow_id),
                "o_baseline_timed": view["assumptions"]["o_timed"],
            }
        )
    return item_list


def gateway_stats(window_hours: float = DEFAULT_WINDOW_HOURS) -> dict[str, Any]:
    """Gateway call counts, outcomes, fallback depths, cache hit rate, and spend by model."""
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    with session() as conn:
        rows = rows_to_dicts(
            conn.execute("SELECT * FROM llm_calls WHERE created_at >= ?", (since,)).fetchall()
        )
    ok_list = [r for r in rows if r["outcome"] == "ok"]
    by_model: dict[str, dict[str, float]] = {}
    for row in ok_list:
        entry = by_model.setdefault(
            row["model"], {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
        )
        entry["calls"] += 1
        entry["cost_usd"] += row["cost_usd"]
        entry["input_tokens"] += row["input_tokens"]
        entry["output_tokens"] += row["output_tokens"]
    cache_counter = Counter(r["cache"] for r in ok_list)
    return {
        "window_hours": window_hours,
        "calls": len(rows),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "fallback_depth_distribution": {
            str(k): v for k, v in sorted(Counter(r["fallback_depth"] for r in ok_list).items())
        },
        "cache": dict(cache_counter),
        "cache_hit_rate": (cache_counter["exact"] + cache_counter["semantic"]) / len(ok_list)
        if ok_list
        else None,
        "by_model": by_model,
        "total_cost_usd": sum(r["cost_usd"] for r in rows),
    }


def guard_log(limit: int = 200) -> list[dict[str, Any]]:
    """Return guard actions newest first."""
    with session() as conn:
        return rows_to_dicts(
            conn.execute(
                "SELECT * FROM guard_actions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        )
