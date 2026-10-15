"""Unit tests for the E4 harness in fake-provider mode (fallback and budget parts)."""

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.settings import get_settings
from roiconsole.evaluation.e4_gateway import run_budget, run_fallback

pytestmark = pytest.mark.unit


# ============= Tests =============


def small_config() -> dict:
    """Return the E4 config in fake mode with few requests and short timeouts."""
    config = load_yaml(config_path("eval/e4.yaml"))
    config["provider_mode"] = "fake"
    config["fallback"]["requests_per_scenario"] = 8
    config["fallback"]["scenarios"] = [
        {"name": "baseline", "kind": "none", "rate": 0.0},
        {"name": "http_500_all", "kind": "http_500", "rate": 1.0},
    ]
    return config


async def test_fallback_scenarios_recover_through_secondary(tmp_path, monkeypatch):
    import roiconsole.gateway.service as service_module

    monkeypatch.setattr(service_module, "backoff_s", lambda *a: 0.0)
    results = await run_fallback(small_config(), get_settings(), tmp_path)
    baseline = results["scenarios"]["baseline"]
    faulted = results["scenarios"]["http_500_all"]
    assert baseline["success"]["rate"] == 1.0 and baseline["fallback_depth_distribution"] == {
        "0": 8
    }
    assert faulted["success"]["rate"] == 1.0 and faulted["fallback_depth_distribution"] == {"1": 8}
    # the breaker opens after five failures, so later requests skip the primary
    assert faulted["circuit_open_skips"] > 0


async def test_budget_runs_halt_without_overspending(tmp_path):
    results = await run_budget(small_config(), tmp_path)
    assert results["enforcement_correct"]["rate"] == 1.0
    assert results["max_overspend_usd"] <= 0
    assert any(r["o_halted"] for r in results["runs"])
