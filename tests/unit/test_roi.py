"""Unit tests for ROI math, break-even points, the sensitivity grid, and E3."""

# ============= Standard Library =============
import json

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.e3_roi import run_e3
from roiconsole.roi.compute import (
    Economics,
    break_even_success_rate,
    sensitivity_grid,
    summarize_runs,
    task_roi,
)

pytestmark = pytest.mark.unit

ECON = Economics(
    minutes_per_task=12,
    labor_rate_usd_per_hour=60,
    review_minutes_per_task=2,
    fix_minutes_per_failure=15,
)


# ============= Per Task =============


def test_successful_task_roi():
    roi = task_roi(1.0, 0.01, ECON)
    # value 12 min at 1 USD/min = 12; human cost = review 2; cost = 2.01
    assert roi.value_usd == pytest.approx(12.0)
    assert roi.human_cost_usd == pytest.approx(2.0)
    assert roi.net_usd == pytest.approx(12.0 - 2.01)


def test_failed_task_is_negative_with_fix_time():
    roi = task_roi(0.0, 0.01, ECON)
    assert roi.value_usd == 0.0
    assert roi.human_cost_usd == pytest.approx(17.0)
    assert roi.net_usd == pytest.approx(-17.01)


def test_success_out_of_range_raises():
    with pytest.raises(ValueError):
        task_roi(1.5, 0.0, ECON)


# ============= Break-Even =============


def test_break_even_success_rate_zeroes_expected_net():
    rate = break_even_success_rate(0.01, ECON)
    expected_net = rate * 12 - (0.01 + 2 + (1 - rate) * 15)
    assert expected_net == pytest.approx(0.0, abs=1e-9)
    assert rate == pytest.approx((0.01 + 17) / 27)


def test_break_even_unreachable_returns_none():
    cheap = Economics(
        minutes_per_task=1,
        labor_rate_usd_per_hour=60,
        review_minutes_per_task=5,
        fix_minutes_per_failure=1,
    )
    assert break_even_success_rate(0.0, cheap) is None


def test_summary_and_break_even_minutes():
    runs = [{"success": True, "llm_cost_usd": 0.02}] * 3 + [
        {"success": False, "llm_cost_usd": 0.02}
    ]
    summary = summarize_runs(runs, ECON)
    cost = 4 * 0.02 + 4 * 2 + 15
    assert summary["cost_usd"] == pytest.approx(cost)
    assert summary["net_roi_usd"] == pytest.approx(36 - cost)
    assert summary["roi_ratio"] == pytest.approx(36 / cost)
    assert summary["cost_per_success_usd"] == pytest.approx(cost / 3)
    # break-even minutes: cost per success * 60 / labor rate
    assert summary["break_even_minutes_per_task"] == pytest.approx(cost / 3)
    assert summary["success"]["ci95_low"] < 0.75 < summary["success"]["ci95_high"]


def test_sensitivity_grid_covers_spec_values():
    runs = [{"success": True, "llm_cost_usd": 0.01}] * 5
    grid = sensitivity_grid(runs, ECON)
    assert {(g["minutes_multiplier"], g["labor_rate_usd_per_hour"]) for g in grid} == {
        (m, r) for m in (0.25, 0.5, 1.0, 1.5) for r in (25.0, 55.0, 100.0)
    }
    low = next(
        g for g in grid if g["minutes_multiplier"] == 0.25 and g["labor_rate_usd_per_hour"] == 25.0
    )
    high = next(
        g for g in grid if g["minutes_multiplier"] == 1.5 and g["labor_rate_usd_per_hour"] == 100.0
    )
    assert low["net_roi_per_task_usd"] < high["net_roi_per_task_usd"]


# ============= E3 =============


def test_e3_reads_e2_records(tmp_path):
    results_dir = get_settings().results_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "workflow_id": "crm_pipeline",
        "path": "pipeline",
        "model_config": "auto",
        "success": True,
        "llm_cost_usd": 0.01,
        "tool_cost_usd": 0.0,
    }
    payload = {
        "run_id": "x",
        "timestamp_utc": "2026-10-14T00:00:00Z",
        "records": [record, {**record, "success": False}],
    }
    (results_dir / "e2_tasks.json").write_text(json.dumps(payload))
    metrics, _, source = run_e3()
    entry = metrics["workflows"]["crm_pipeline"]["by_path_and_config"]["pipeline"]["auto"]
    assert entry["n_runs"] == 2 and entry["success"]["rate"] == 0.5
    assert len(metrics["workflows"]["crm_pipeline"]["sensitivity"]["by_path"]["pipeline"]) == 12
    assert source["e2_run_id"] == "x"
    assert metrics["workflows"]["slack_digest"]["by_path_and_config"] == {}
