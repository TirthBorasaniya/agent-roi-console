"""E5: the ROI guard on simulated run streams, checking actions at the expected run counts."""

# ============= Standard Library =============
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.db import init_db, insert_row, session
from roiconsole.guard.service import GuardService
from roiconsole.guard.state_machine import ACTION_NONE
from roiconsole.workflows.registry import load_workflows

# ============= Constants =============
E5_CONFIG = "eval/e5.yaml"
SIM_TRIGGER = "simulated"


# ============= Simulation =============


def insert_simulated_run(
    workflow_id: str, path: str, success: bool, cost: float, started_at: datetime
) -> None:
    """Insert one completed synthetic production run."""
    with session() as conn:
        insert_row(
            conn,
            "runs",
            {
                "run_id": f"sim_{uuid.uuid4().hex[:12]}",
                "workflow_id": workflow_id,
                "task_id": None,
                "path": path,
                "model_config": "auto",
                "trigger": SIM_TRIGGER,
                "status": "COMPLETED" if success else "FAILED",
                "success": int(success),
                "llm_cost_usd": cost,
                "tool_cost_usd": 0.0,
                "latency_s": 1.0,
                "started_at": started_at.isoformat(),
                "completed_at": started_at.isoformat(),
            },
        )


async def run_stream(
    stream_dict: dict[str, Any], cost: float, guard: GuardService
) -> dict[str, Any]:
    """
    Feed one stream to the guard, evaluating after every run.

    Returns
    -------
    result : dict
        Observed actions with run counts, expected actions, alerts, and o_passed.
    """
    workflow = load_workflows()[stream_dict["workflow_id"]]
    observed_list: list[dict[str, Any]] = []
    start = datetime.now(UTC) - timedelta(hours=1)
    n_runs = 0
    for segment in stream_dict["segments"]:
        for _ in range(segment["runs"]):
            n_runs += 1
            insert_simulated_run(
                workflow.workflow_id,
                workflow.default_path,
                segment["success"],
                cost,
                start + timedelta(seconds=n_runs),
            )
            decision = await guard.evaluate_workflow(workflow.workflow_id)
            if decision.action != ACTION_NONE:
                observed_list.append(
                    {
                        "action": decision.action,
                        "at_run": n_runs,
                        "net_roi_usd": decision.net_roi_usd,
                        "detail": decision.detail,
                    }
                )
    expected_list = stream_dict["expected"]
    o_passed = [(o["action"], o["at_run"]) for o in observed_list] == [
        (e["action"], e["at_run"]) for e in expected_list
    ]
    return {
        "workflow_id": workflow.workflow_id,
        "runs": n_runs,
        "expected": expected_list,
        "observed": observed_list,
        "o_passed": o_passed,
    }


async def run_e5(config_file: Path | None = None) -> tuple[dict[str, Any], Path]:
    """
    Run every configured stream against a fresh database.

    The caller points ROI_DB_PATH at a scratch file so production runs are untouched.
    """
    config_file = config_file or config_path(E5_CONFIG)
    config_dict = load_yaml(config_file)
    init_db()
    alert_list: list[dict[str, str]] = []

    async def notifier(workflow_id: str, text: str) -> None:
        alert_list.append({"workflow_id": workflow_id, "text": text})

    guard = GuardService(notifier)
    stream_results = {
        s["name"]: await run_stream(s, config_dict["llm_cost_usd_per_run"], guard)
        for s in config_dict["streams"]
    }
    n_passed = sum(r["o_passed"] for r in stream_results.values())
    return (
        {
            "streams": stream_results,
            "streams_passed": n_passed,
            "streams_total": len(stream_results),
            "alerts": alert_list,
            "min_runs": guard.config.min_runs,
        },
        config_file,
    )
