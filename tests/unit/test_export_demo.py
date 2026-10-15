"""Unit tests for the static demo export: it refuses without results and mirrors the API shapes."""

# ============= Standard Library =============
import json
import runpy
import sys

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.settings import REPO_ROOT, get_settings

pytestmark = pytest.mark.unit
SCRIPT = str(REPO_ROOT / "scripts" / "export_demo_data.py")


# ============= Tests =============


def test_export_refuses_without_e2(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["export_demo_data.py"])
    with pytest.raises(SystemExit, match="e2_tasks.json is missing"):
        runpy.run_path(SCRIPT, run_name="__main__")


def test_export_writes_api_shaped_files(tmp_path, monkeypatch):
    results_dir = get_settings().results_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": "run_1",
        "task_id": "crm_pipeline_003",
        "workflow_id": "crm_pipeline",
        "path": "pipeline",
        "model_config": "auto",
        "success": True,
        "failure_reason": None,
        "validator_results": [],
        "llm_cost_usd": 0.001,
        "tool_cost_usd": 0.0,
        "latency_s": 2.0,
        "models_used": {"groq/openai/gpt-oss-20b": 2},
    }
    payload = {"run_id": "x", "timestamp_utc": "2026-10-20T00:00:00Z", "records": [record]}
    (results_dir / "e2_tasks.json").write_text(json.dumps(payload))
    out_dir = tmp_path / "demo-data"
    monkeypatch.setattr(sys, "argv", ["export_demo_data.py"])
    module_globals = runpy.run_path(SCRIPT, run_name="not_main")
    module_globals["main"].__globals__["OUT_DIR"] = out_dir
    module_globals["main"]()
    overview = json.loads((out_dir / "overview.json").read_text())
    crm = next(o for o in overview if o["workflow_id"] == "crm_pipeline")
    assert crm["n_runs"] == 1 and crm["success"]["rate"] == 1.0
    meta = json.loads((out_dir / "meta.json").read_text())
    assert meta["o_static"] and meta["snapshot_date"] == "2026-10-20"
    assert json.loads((out_dir / "workflow-crm_pipeline.json").read_text())["model_mix"] == {
        "groq/openai/gpt-oss-20b": 2
    }
    assert (out_dir / "run-run_1.json").is_file() and (out_dir / "guard.json").is_file()
