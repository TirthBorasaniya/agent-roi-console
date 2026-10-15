"""Guard service: evaluate every workflow on a schedule, persist actions, and alert on pauses."""

# ============= Standard Library =============
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

# ============= Local =============
from roiconsole.common import metrics
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.db import insert_row, session, utc_now
from roiconsole.common.results import read_results
from roiconsole.guard.state_machine import (
    ACTION_NONE,
    ACTION_PAUSE,
    GuardConfig,
    GuardDecision,
    GuardState,
    PathStats,
    evaluate,
    window_for,
)
from roiconsole.roi.compute import Economics, task_roi
from roiconsole.workflows.registry import load_workflows

# ============= Constants =============
logger = logging.getLogger(__name__)
GUARD_FILE = "guard.yaml"
Notifier = Callable[[str, str], Awaitable[None]]


# ============= State Persistence =============


def load_state(workflow_id: str, default_path: str) -> GuardState:
    """Read a workflow's guard state, creating the default row when missing."""
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM workflow_state WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO workflow_state VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workflow_id, default_path, "ACTIVE", "normal", utc_now(), 0, utc_now()),
            )
            return GuardState(workflow_id, default_path)
    return GuardState(workflow_id, row["active_path"], row["guard_phase"], row["phase_run_offset"])


def save_state(state: GuardState) -> None:
    """Persist a guard state."""
    status = "PAUSED" if state.phase == "paused" else "ACTIVE"
    with session() as conn:
        conn.execute(
            "UPDATE workflow_state SET active_path = ?, status = ?, guard_phase = ?, "
            "phase_started_at = ?, "
            "phase_run_offset = ?, updated_at = ? WHERE workflow_id = ?",
            (
                state.active_path,
                status,
                state.phase,
                utc_now(),
                state.phase_run_offset,
                utc_now(),
                state.workflow_id,
            ),
        )


def record_action(decision: GuardDecision, previous: GuardState) -> None:
    """Write a guard_actions row."""
    with session() as conn:
        insert_row(
            conn,
            "guard_actions",
            {
                "workflow_id": previous.workflow_id,
                "action": decision.action,
                "from_path": previous.active_path,
                "to_path": decision.new_state.active_path,
                "window_runs": decision.window_runs,
                "net_roi_usd": decision.net_roi_usd,
                "detail": decision.detail,
                "created_at": utc_now(),
            },
        )


# ============= Inputs =============


def trailing_runs(workflow_id: str, window_hours: float) -> tuple[list[dict[str, Any]], int]:
    """
    Return completed non-demo runs in the window (oldest first, with seq) and the total run count.

    Evaluation runs are excluded: the guard acts on production traffic.
    """
    since = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    with session() as conn:
        rows = conn.execute(
            "SELECT run_id, path, success, llm_cost_usd, tool_cost_usd, started_at FROM runs "
            "WHERE workflow_id = ? AND status IN ('COMPLETED', 'FAILED') "
            "AND trigger NOT LIKE 'eval%' "
            "ORDER BY started_at",
            (workflow_id,),
        ).fetchall()
    run_list = [{**dict(r), "seq": i} for i, r in enumerate(rows)]
    return [r for r in run_list if r["started_at"] >= since], len(run_list)


def path_stats_for(
    workflow_id: str, run_list: list[dict[str, Any]], min_runs_per_path: int
) -> dict[str, PathStats]:
    """
    Path success and cost: from E2's routed configuration when available, else trailing runs.
    """
    e2 = read_results("e2_tasks")
    if e2:
        by_path = e2["metrics"]["by_workflow"].get(workflow_id, {})
        stats_dict = {
            path: PathStats(
                configs["auto"]["success"]["rate"] or 0.0,
                configs["auto"]["mean_cost_per_task_usd"] or 0.0,
            )
            for path, configs in by_path.items()
            if "auto" in configs
        }
        if stats_dict:
            return stats_dict
    stats_dict = {}
    for path in {r["path"] for r in run_list}:
        subset = [r for r in run_list if r["path"] == path]
        if len(subset) >= min_runs_per_path:
            stats_dict[path] = PathStats(
                sum(bool(r["success"]) for r in subset) / len(subset),
                sum(r["llm_cost_usd"] + r["tool_cost_usd"] for r in subset) / len(subset),
            )
    return stats_dict


# ============= Service =============


class GuardService:
    """
    Runs the guard for every workflow.

    Parameters
    ----------
    notifier : callable or None
        `await notifier(workflow_id, text)` posts the pause alert (Slack in
        production); None logs only.
    """

    def __init__(self, notifier: Notifier | None = None) -> None:
        data = load_yaml(config_path(GUARD_FILE))
        self.interval_s = float(data["interval_s"])
        self.window_hours = float(data["window_hours"])
        self.min_runs_per_path = int(data["min_runs_per_path_for_stats"])
        self.config = GuardConfig(int(data["min_runs"]), float(data["path_success_bar"]))
        self.notifier = notifier

    async def evaluate_workflow(self, workflow_id: str) -> GuardDecision:
        """Evaluate one workflow and apply the decision."""
        workflow = load_workflows()[workflow_id]
        economics = Economics.from_workflow(workflow)
        state = load_state(workflow_id, workflow.default_path)
        run_list, total_runs = trailing_runs(workflow_id, self.window_hours)
        window_list = window_for(state, run_list, self.config.min_runs)
        net_list = [
            task_roi(
                float(bool(r["success"])), r["llm_cost_usd"], economics, r["tool_cost_usd"]
            ).net_usd
            for r in window_list
        ]
        decision = evaluate(
            state,
            net_list,
            total_runs,
            path_stats_for(workflow_id, run_list, self.min_runs_per_path),
            self.config,
        )
        if decision.action != ACTION_NONE:
            save_state(decision.new_state)
            record_action(decision, state)
            metrics.GUARD_ACTIONS_TOTAL.labels(workflow=workflow_id, action=decision.action).inc()
            logger.info(
                "guard action", extra={"workflow_id": workflow_id, "action": decision.action}
            )
            if decision.action == ACTION_PAUSE and self.notifier is not None:
                await self.notifier(
                    workflow_id, f"ROI guard paused {workflow.name}: {decision.detail}"
                )
        return decision

    async def evaluate_all(self) -> dict[str, GuardDecision]:
        """Evaluate every workflow once."""
        return {wf: await self.evaluate_workflow(wf) for wf in load_workflows()}

    async def run_forever(self) -> None:
        """Evaluate every interval until cancelled."""
        while True:
            try:
                await self.evaluate_all()
            except Exception:
                # keep the scheduler alive; the failure is logged with its traceback
                logger.exception("guard evaluation failed")
            await asyncio.sleep(self.interval_s)
