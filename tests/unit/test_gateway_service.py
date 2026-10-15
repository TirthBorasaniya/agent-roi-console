"""Unit tests for the gateway service: fallback, budget downgrade and halt, caching, telemetry."""

# ============= Third-Party =============
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# ============= Local =============
from roiconsole.common.db import connect, init_db
from roiconsole.gateway.adapters.fault import FaultPlan
from roiconsole.gateway.app import create_app
from roiconsole.gateway.schemas import ChatCompletionRequest
from roiconsole.gateway.service import GatewayError
from roiconsole.gateway.telemetry import setup_tracing
from tests.gateway_helpers import CHEAP, MID, PRICEY, build_test_service

pytestmark = pytest.mark.unit


# ============= Helpers =============


def make_request(
    model: str = "auto", step_class: str = "extraction", run_id: str = "run1", **kwargs
):
    """Return a routed request with metadata."""
    return ChatCompletionRequest(
        model=model,
        messages=[{"role": "user", "content": kwargs.pop("content", "extract the date")}],
        max_tokens=kwargs.pop("max_tokens", 100),
        metadata={
            "workflow_id": "meeting_notes",
            "run_id": run_id,
            "step_id": "extract_meeting",
            "step_class": step_class,
            **kwargs.pop("metadata", {}),
        },
        **kwargs,
    )


@pytest.fixture(autouse=True)
def db():
    """Create the console database in the temp data dir."""
    init_db()


# ============= Routing and Fallback =============


async def test_routed_request_uses_cheapest_qualifying_model(tmp_path):
    service, _ = build_test_service(tmp_path)
    response = await service.complete(make_request())
    assert response["x_roi"]["model"] == CHEAP
    assert response["x_roi"]["fallback_depth"] == 0
    assert response["x_roi"]["cost_usd"] > 0


async def test_failures_fall_back_to_next_candidate(tmp_path):
    service, adapters = build_test_service(tmp_path)
    adapters[CHEAP].set_plan(FaultPlan("http_500", 1.0))
    response = await service.complete(make_request())
    roi = response["x_roi"]
    assert roi["model"] == MID and roi["fallback_depth"] == 1
    # first model: one try plus two retries, all 500s
    assert [a["outcome"] for a in roi["attempts"]] == ["http_500"] * 3 + ["ok"]


async def test_non_retryable_error_skips_retries(tmp_path):
    service, adapters = build_test_service(tmp_path)

    async def bad_request(_request):
        from roiconsole.gateway.adapters.base import status_error

        raise status_error(400, "bad")

    adapters[CHEAP].complete = bad_request
    response = await service.complete(make_request())
    assert [a["outcome"] for a in response["x_roi"]["attempts"]] == ["http_400", "ok"]


async def test_timeouts_are_cut_by_gateway_timeout(tmp_path):
    service, adapters = build_test_service(tmp_path, timeout_s=0.05)
    adapters[CHEAP].set_plan(FaultPlan("timeout", 1.0, hang_s=10))
    response = await service.complete(make_request())
    assert response["x_roi"]["model"] == MID
    assert response["x_roi"]["attempts"][0]["outcome"] == "timeout"


async def test_open_circuit_is_skipped(tmp_path):
    service, adapters = build_test_service(tmp_path)
    adapters[CHEAP].set_plan(FaultPlan("http_429", 1.0))
    await service.complete(make_request(content="a"))
    await service.complete(make_request(content="b"))
    calls_before = adapters[CHEAP].injected
    response = await service.complete(make_request(content="c"))
    assert adapters[CHEAP].injected == calls_before
    assert response["x_roi"]["attempts"][0]["outcome"] == "circuit_open"


async def test_all_candidates_failing_returns_502_and_records(tmp_path):
    service, adapters = build_test_service(tmp_path)
    for adapter in adapters.values():
        adapter.set_plan(FaultPlan("http_500", 1.0))
    with pytest.raises(GatewayError) as excinfo:
        await service.complete(make_request())
    assert excinfo.value.status == 502
    with connect() as conn:
        outcome = conn.execute("SELECT outcome FROM llm_calls").fetchone()["outcome"]
    assert outcome == "error"


async def test_explicit_model_is_single_model_without_fallback(tmp_path):
    service, adapters = build_test_service(tmp_path)
    adapters[MID].set_plan(FaultPlan("http_500", 1.0))
    with pytest.raises(GatewayError):
        await service.complete(make_request(model=MID))
    assert adapters[CHEAP]._inner.calls == 0


async def test_unknown_model_and_missing_metadata_are_rejected(tmp_path):
    service, _ = build_test_service(tmp_path)
    with pytest.raises(GatewayError) as excinfo:
        await service.complete(make_request(model="fake/none"))
    assert excinfo.value.code == "unknown_model"
    request = ChatCompletionRequest(model="auto", messages=[{"role": "user", "content": "x"}])
    with pytest.raises(GatewayError) as excinfo:
        await service.complete(request)
    assert excinfo.value.code == "missing_metadata"


# ============= Budgets =============


async def test_budget_downgrades_when_chosen_model_does_not_fit(tmp_path):
    # MID is cheapest qualifying; its estimate for 1,000 max tokens exceeds the budget, CHEAP's fits
    service, _ = build_test_service(
        tmp_path, per_run_usd=0.002, success_dict={CHEAP: 0.95, MID: 0.97, PRICEY: 0.99}
    )
    service.deps.routing_table = None
    from tests.gateway_helpers import routing_table

    table = routing_table(tmp_path, {CHEAP: 0.95, MID: 0.97, PRICEY: 0.99})
    # measured mean cost (E1 tokens) can order models differently from the max_tokens estimate
    table._candidates_dict["extraction"].sort(key=lambda c: -c.mean_cost_usd)
    service.deps.routing_table = table
    response = await service.complete(make_request(max_tokens=1000))
    assert response["x_roi"]["downgraded"] is True
    assert response["x_roi"]["model"] == CHEAP


async def test_budget_exhausted_halts_and_counts(tmp_path):
    service, _ = build_test_service(tmp_path, per_run_usd=0.0000001)
    with pytest.raises(GatewayError) as excinfo:
        await service.complete(make_request(max_tokens=1000))
    assert excinfo.value.status == 402 and excinfo.value.code == "budget_exhausted"
    with connect() as conn:
        assert (
            conn.execute("SELECT outcome FROM llm_calls").fetchone()["outcome"]
            == "budget_exhausted"
        )


async def test_spend_accumulates_per_run(tmp_path):
    service, _ = build_test_service(tmp_path)
    first = await service.complete(make_request(content="a"))
    second = await service.complete(make_request(content="b"))
    spent = first["x_roi"]["cost_usd"] + second["x_roi"]["cost_usd"]
    run_spent, _ = await service.deps.budgets.spent("meeting_notes", "run1")
    assert run_spent == pytest.approx(spent)


# ============= Caching =============


async def test_exact_cache_hit_is_free_and_labeled(tmp_path):
    service, adapters = build_test_service(tmp_path)
    await service.complete(make_request())
    response = await service.complete(make_request(run_id="run2"))
    assert response["x_roi"]["cache"] == "exact" and response["x_roi"]["cost_usd"] == 0.0


async def test_cache_off_bypasses_cache(tmp_path):
    service, _ = build_test_service(tmp_path)
    await service.complete(make_request(metadata={"cache": "off"}))
    response = await service.complete(make_request(metadata={"cache": "off"}))
    assert response["x_roi"]["cache"] == "miss"


class FakeSemantic:
    """Semantic cache double that hits on any stored step class and model."""

    def __init__(self) -> None:
        self.store_dict = {}

    async def get(self, text, step_class, model):
        return self.store_dict.get((step_class, model))

    async def put(self, text, step_class, model, response_dict):
        self.store_dict[(step_class, model)] = response_dict


async def test_semantic_cache_only_for_cacheable_step_classes(tmp_path):
    service, _ = build_test_service(tmp_path)
    service.deps.semantic_cache = FakeSemantic()
    await service.complete(make_request(step_class="extraction", content="first"))
    hit = await service.complete(make_request(step_class="extraction", content="second"))
    assert hit["x_roi"]["cache"] == "semantic"
    await service.complete(make_request(step_class="summarization", content="third"))
    miss = await service.complete(make_request(step_class="summarization", content="fourth"))
    assert miss["x_roi"]["cache"] == "miss"


# ============= Telemetry and API =============


async def test_span_carries_genai_and_roi_attributes(tmp_path):
    exporter = InMemorySpanExporter()
    provider = setup_tracing("test", "", exporter=exporter)
    service, _ = build_test_service(tmp_path)
    await service.complete(make_request())
    provider.force_flush()
    span = exporter.get_finished_spans()[-1]
    assert span.name == f"chat {CHEAP}"
    for key in (
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "roi.workflow_id",
        "roi.run_id",
        "roi.step_id",
        "roi.cost_usd",
        "roi.cache",
        "roi.fallback_depth",
    ):
        assert key in span.attributes


def test_http_api_serves_completion_stream_models_and_metrics(tmp_path):
    service, _ = build_test_service(tmp_path)
    with TestClient(create_app(service)) as client:
        body = make_request().model_dump()
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"]
        stream = client.post("/v1/chat/completions", json={**body, "stream": True})
        assert stream.text.strip().endswith("data: [DONE]")
        bad = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "x"}]},
        )
        assert bad.status_code == 400
        assert {m["id"] for m in client.get("/v1/models").json()["data"]} >= {
            "auto",
            CHEAP,
            MID,
            PRICEY,
        }
        assert client.get("/healthz").json()["status"] == "ok"
        assert "roiconsole_llm_calls_total" in client.get("/metrics").text
