"""Unit tests for the guard state machine, the guard service, and E5."""

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.db import connect, init_db
from roiconsole.evaluation.e5_guard import insert_simulated_run, run_e5
from roiconsole.guard.service import GuardService, load_state
from roiconsole.guard.state_machine import (
    ACTION_DOWNGRADE,
    ACTION_NONE,
    ACTION_PAUSE,
    ACTION_RECOVER,
    GuardConfig,
    GuardState,
    PathStats,
    cheapest_qualifying_path,
    evaluate,
    window_for,
)

pytestmark = pytest.mark.unit

STATS = {
    "react": PathStats(0.95, 0.05),
    "pipeline": PathStats(0.92, 0.01),
    "planner_executor": PathStats(0.6, 0.005),
}
CONFIG = GuardConfig(min_runs=20, path_success_bar=0.9)


# ============= State Machine =============


def test_no_action_below_min_runs():
    decision = evaluate(GuardState("wf", "react"), [-1.0] * 19, 19, STATS, CONFIG)
    assert decision.action == ACTION_NONE


def test_negative_roi_downgrades_to_cheapest_qualifying_path():
    decision = evaluate(GuardState("wf", "react"), [-1.0] * 20, 20, STATS, CONFIG)
    assert decision.action == ACTION_DOWNGRADE
    assert decision.new_state.active_path == "pipeline"
    assert decision.new_state.phase_run_offset == 20


def test_cheapest_qualifying_ignores_paths_below_bar():
    assert cheapest_qualifying_path(STATS, 0.9) == "pipeline"
    assert cheapest_qualifying_path(STATS, 0.99) is None


def test_still_negative_after_downgrade_pauses_and_positive_recovers():
    state = GuardState("wf", "pipeline", "downgraded", 20)
    assert evaluate(state, [-1.0] * 20, 40, STATS, CONFIG).action == ACTION_PAUSE
    assert evaluate(state, [1.0] * 20, 40, STATS, CONFIG).action == ACTION_RECOVER
    assert evaluate(state, [-1.0] * 19, 39, STATS, CONFIG).action == ACTION_NONE


def test_paused_stays_paused():
    assert (
        evaluate(GuardState("wf", "pipeline", "paused", 40), [-1.0] * 30, 70, STATS, CONFIG).action
        == ACTION_NONE
    )


def test_downgraded_window_counts_only_runs_after_the_action():
    run_list = [{"seq": i} for i in range(45)]
    window = window_for(GuardState("wf", "p", "downgraded", 20), run_list, 20)
    assert [r["seq"] for r in window] == list(range(20, 40))
    assert [r["seq"] for r in window_for(GuardState("wf", "p"), run_list, 20)] == list(
        range(25, 45)
    )


# ============= Service =============


async def test_service_persists_action_and_alerts_on_pause():
    from datetime import UTC, datetime, timedelta

    init_db()
    alerts = []

    async def notifier(workflow_id, text):
        alerts.append(workflow_id)

    guard = GuardService(notifier)
    start = datetime.now(UTC) - timedelta(minutes=30)
    for i in range(40):
        insert_simulated_run("crm_pipeline", "pipeline", False, 0.001, start + timedelta(seconds=i))
        await guard.evaluate_workflow("crm_pipeline")
    assert load_state("crm_pipeline", "pipeline").phase == "paused"
    assert alerts == ["crm_pipeline"]
    with connect() as conn:
        actions = [
            r["action"] for r in conn.execute("SELECT action FROM guard_actions ORDER BY id")
        ]
    assert actions == ["downgrade", "pause"]


async def test_eval_runs_are_ignored_by_the_guard():
    init_db()
    from roiconsole.common.db import insert_row, session, utc_now

    with session() as conn:
        for i in range(25):
            insert_row(
                conn,
                "runs",
                {
                    "run_id": f"e{i}",
                    "workflow_id": "slack_digest",
                    "path": "pipeline",
                    "model_config": "auto",
                    "trigger": "eval_e2",
                    "status": "FAILED",
                    "success": 0,
                    "started_at": utc_now(),
                },
            )
    decision = await GuardService().evaluate_workflow("slack_digest")
    assert decision.action == ACTION_NONE and decision.window_runs == 0


# ============= E5 =============


async def test_e5_streams_act_at_expected_run_counts():
    metrics, _ = await run_e5()
    assert metrics["streams_passed"] == metrics["streams_total"] == 3, metrics["streams"]
    assert [a["workflow_id"] for a in metrics["alerts"]] == ["crm_pipeline"]
