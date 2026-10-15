"""Integration test: the gateway over HTTP with Redis and a failing fake provider."""

# ============= Third-Party =============
import pytest
from fastapi.testclient import TestClient

# ============= Local =============
from roiconsole.common.settings import get_settings
from roiconsole.gateway.adapters.fault import FaultPlan
from roiconsole.gateway.app import create_app
from roiconsole.gateway.cache import ExactCache
from roiconsole.gateway.store import RedisStore
from tests.gateway_helpers import CHEAP, MID, build_test_service

pytestmark = pytest.mark.integration


# ============= Tests =============


def test_gateway_fails_over_and_caches_through_redis(tmp_path, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    get_settings.cache_clear()
    service, adapters = build_test_service(tmp_path)
    store = RedisStore.from_url(get_settings().redis_url, prefix=f"it-{tmp_path.name}:")
    service.deps.exact_cache = ExactCache(store, 60)
    service.deps.budgets._store = store
    adapters[CHEAP].set_plan(FaultPlan("http_500", 1.0))
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": "integration"}],
        "max_tokens": 50,
        "metadata": {
            "workflow_id": "wf",
            "run_id": "it-run",
            "step_id": "s",
            "step_class": "extraction",
        },
    }
    with TestClient(create_app(service)) as client:
        first = client.post("/v1/chat/completions", json=body).json()
        second = client.post("/v1/chat/completions", json=body).json()
    assert first["x_roi"]["model"] == MID and first["x_roi"]["fallback_depth"] == 1
    assert second["x_roi"]["cache"] == "exact"
