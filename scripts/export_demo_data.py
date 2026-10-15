"""Export the static demo snapshot: API-shaped JSON from committed results, for the Pages build."""

# ============= Standard Library =============
import argparse
import json
import shutil
from typing import Any

# ============= Local =============
from roiconsole import __version__
from roiconsole.common.results import read_results
from roiconsole.common.settings import REPO_ROOT, get_settings
from roiconsole.roi.compute import Economics, sensitivity_grid, summarize_runs
from roiconsole.workflows.registry import load_workflows

# ============= Constants =============
OUT_DIR = REPO_ROOT / "frontend" / "public" / "demo-data"
ROUTED = "auto"
RESULT_NAMES = (
    "e1_step_success",
    "e2_tasks",
    "e3_roi",
    "e4_gateway",
    "e5_guard",
    "inventory",
    "live_tasks",
    "selfhosted_throughput",
)


# ============= Builders =============


def run_summary(record: dict[str, Any], started_at: str) -> dict[str, Any]:
    """Shape an E2 record like an API run row."""
    return {
        "run_id": record["run_id"],
        "workflow_id": record["workflow_id"],
        "task_id": record["task_id"],
        "path": record["path"],
        "model_config": record["model_config"],
        "trigger": "eval_e2",
        "status": "COMPLETED" if record["success"] else "FAILED",
        "success": record["success"],
        "failure_reason": record["failure_reason"],
        "validator_results": record["validator_results"],
        "llm_cost_usd": record["llm_cost_usd"],
        "latency_s": record["latency_s"],
        "started_at": started_at,
    }


def run_detail_from_db(record: dict[str, Any], started_at: str) -> dict[str, Any]:
    """Add step traces, tool calls, and model calls from the evaluation database when present."""
    detail = {**run_summary(record, started_at), "steps": [], "tool_calls": [], "llm_calls": []}
    if not get_settings().resolved_db_path.is_file():
        return detail
    from roiconsole.api.queries import run_detail

    stored = run_detail(record["run_id"])
    if stored:
        detail.update(
            steps=stored["steps"], tool_calls=stored["tool_calls"], llm_calls=stored["llm_calls"]
        )
    return detail


def gateway_snapshot(record_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Gateway stats over E2's model calls, from the evaluation database when present."""
    if get_settings().resolved_db_path.is_file():
        from roiconsole.api.queries import gateway_stats

        stats = gateway_stats(window_hours=24 * 365)
        stats["breakers"] = None
        return stats
    by_model: dict[str, dict[str, float]] = {}
    for record in record_list:
        for model, calls in record["models_used"].items():
            entry = by_model.setdefault(
                model, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}
            )
            entry["calls"] += calls
    return {
        "window_hours": 0,
        "calls": sum(int(e["calls"]) for e in by_model.values()),
        "outcomes": {},
        "fallback_depth_distribution": {},
        "cache": {},
        "cache_hit_rate": None,
        "by_model": by_model,
        "total_cost_usd": sum(r["llm_cost_usd"] for r in record_list),
        "breakers": None,
    }


def guard_snapshot() -> list[dict[str, Any]]:
    """Guard actions observed in E5's simulated streams."""
    e5 = read_results("e5_guard")
    if e5 is None:
        return []
    action_list = []
    for stream_name, stream in e5["metrics"]["streams"].items():
        for observed in stream["observed"]:
            action_list.append(
                {
                    "id": len(action_list) + 1,
                    "workflow_id": stream["workflow_id"],
                    "action": observed["action"],
                    "from_path": None,
                    "to_path": None,
                    "window_runs": 20,
                    "net_roi_usd": observed["net_roi_usd"],
                    "detail": (
                        f"E5 simulated stream '{stream_name}', run {observed['at_run']}: "
                        f"{observed['detail']}"
                    ),
                    "created_at": e5["timestamp_utc"],
                }
            )
    return action_list


# ============= Entry Point =============


def write_json(name: str, payload: Any) -> None:
    """Write one demo data file."""
    (OUT_DIR / f"{name}.json").write_text(json.dumps(payload, default=str), encoding="utf-8")


def main() -> None:
    """Export every file the static API reads, refusing when E2 has not been run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    e2 = read_results("e2_tasks")
    if e2 is None:
        raise SystemExit("results/e2_tasks.json is missing; the demo shows only real measurements")
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    OUT_DIR.mkdir(parents=True)
    started_at = e2["timestamp_utc"]
    record_list = e2["records"]
    overview_list, workflow_list = [], []
    for workflow_id, workflow in load_workflows().items():
        economics = Economics.from_workflow(workflow)
        routed_list = [
            r
            for r in record_list
            if r["workflow_id"] == workflow_id and r["model_config"] == ROUTED
        ]
        default_list = [r for r in routed_list if r["path"] == workflow.default_path]
        model_mix: dict[str, int] = {}
        for record in routed_list:
            for model, calls in record["models_used"].items():
                model_mix[model] = model_mix.get(model, 0) + calls
        view = {
            "workflow_id": workflow_id,
            "name": workflow.name,
            "description": workflow.description,
            "value_category": workflow.value_category,
            "default_path": workflow.default_path,
            "active_path": workflow.default_path,
            "guard_phase": "normal",
            "o_paused": False,
            "steps": [
                {"step_id": s.step_id, "type": s.type, "tool": s.tool, "step_class": s.step_class}
                for s in workflow.steps
            ],
            "assumptions": {
                "minutes_per_task": economics.minutes_per_task,
                "minutes_source": workflow.human_baseline.source,
                "o_timed": workflow.human_baseline.o_timed,
                "labor_rate_usd_per_hour": economics.labor_rate_usd_per_hour,
                "review_minutes_per_task": economics.review_minutes_per_task,
                "fix_minutes_per_failure": economics.fix_minutes_per_failure,
            },
            "model_mix": model_mix,
        }
        workflow_list.append(view)
        write_json(f"workflow-{workflow_id}", view)
        overall = summarize_runs(default_list, economics)
        by_path = {
            path: summarize_runs([r for r in routed_list if r["path"] == path], economics)
            for path in sorted({r["path"] for r in routed_list})
        }
        write_json(
            f"roi-{workflow_id}",
            {"workflow_id": workflow_id, "window_hours": 0, "overall": overall, "by_path": by_path},
        )
        write_json(
            f"sensitivity-{workflow_id}",
            {
                "workflow_id": workflow_id,
                "baseline_minutes_per_task": economics.minutes_per_task,
                "labor_rate_usd_per_hour": economics.labor_rate_usd_per_hour,
                "grid": sensitivity_grid(default_list, economics) if default_list else [],
            },
        )
        write_json(f"runs-{workflow_id}", [run_summary(r, started_at) for r in routed_list])
        for record in routed_list:
            write_json(f"run-{record['run_id']}", run_detail_from_db(record, started_at))
        overview_list.append(
            {
                "workflow_id": workflow_id,
                "name": workflow.name,
                "active_path": workflow.default_path,
                "guard_phase": "normal",
                "n_runs": overall.get("n_runs", 0),
                "success": overall.get("success"),
                "net_roi_usd": overall.get("net_roi_usd"),
                "roi_ratio": overall.get("roi_ratio"),
                "cost_per_success_usd": overall.get("cost_per_success_usd"),
                "machine_cost_per_success_usd": overall.get("machine_cost_per_success_usd"),
                "break_even_success_rate": overall.get("break_even_success_rate"),
                "budget": None,
                "o_baseline_timed": workflow.human_baseline.o_timed,
            }
        )
    write_json("overview", overview_list)
    write_json("workflows", workflow_list)
    write_json("gateway", gateway_snapshot(record_list))
    write_json("guard", guard_snapshot())
    write_json(
        "meta",
        {
            "version": __version__,
            "connector_mode": "static snapshot",
            "o_demo": True,
            "o_static": True,
            "snapshot_date": started_at[:10],
            "results": {
                name: (r or {}).get("timestamp_utc")
                for name in RESULT_NAMES
                for r in [read_results(name)]
            },
        },
    )
    print(f"wrote demo data to {OUT_DIR}")


if __name__ == "__main__":
    main()
