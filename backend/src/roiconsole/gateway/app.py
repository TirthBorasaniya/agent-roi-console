"""Gateway HTTP API: POST /v1/chat/completions, GET /v1/models, /healthz, /metrics."""

# ============= Standard Library =============
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

# ============= Third-Party =============
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.db import init_db
from roiconsole.common.logging import configure_logging
from roiconsole.common.metrics import REGISTRY
from roiconsole.common.settings import REPO_ROOT, Settings, get_settings
from roiconsole.gateway.adapters.anthropic import AnthropicAdapter
from roiconsole.gateway.adapters.base import Adapter
from roiconsole.gateway.adapters.openai_compatible import OpenAICompatibleAdapter
from roiconsole.gateway.adapters.replay import RECORDINGS_FILE, Recorder, ReplayAdapter
from roiconsole.gateway.breaker import BreakerRegistry, CircuitBreaker
from roiconsole.gateway.budgets import BudgetConfig, BudgetTracker
from roiconsole.gateway.cache import ExactCache, RedisSemanticCache, fastembed_vectorizer
from roiconsole.gateway.pricing import PriceTable
from roiconsole.gateway.ratelimit import RateLimit, WorkflowRateLimiter
from roiconsole.gateway.router import RoutingTable, RoutingTableMissingError
from roiconsole.gateway.schemas import ChatCompletionRequest, stream_chunks
from roiconsole.gateway.service import CallSettings, GatewayDeps, GatewayError, GatewayService
from roiconsole.gateway.store import GatewayStore, MemoryStore, RedisStore
from roiconsole.gateway.telemetry import setup_tracing
from roiconsole.workflows.registry import load_step_classes

# ============= Constants =============
logger = logging.getLogger(__name__)
GATEWAY_FILE = "gateway.yaml"
RECORDINGS_DIR = REPO_ROOT / "eval" / "fixtures" / "recordings"
EMBEDDING_CACHE_DIR = REPO_ROOT / ".cache" / "fastembed"


# ============= Builders =============


def build_adapters(settings: Settings, gateway_dict: dict[str, Any]) -> dict[str, Adapter]:
    """
    Build one adapter per configured provider.

    Providers without credentials are left out, so their models fail over.
    In demo mode with no provider keys, every provider replays recordings.
    """
    if settings.o_demo_mode and not (settings.groq_api_key or settings.anthropic_api_key):
        recordings = RECORDINGS_DIR / RECORDINGS_FILE
        return {name: ReplayAdapter(recordings, name) for name in gateway_dict["providers"]}
    adapters_dict: dict[str, Adapter] = {}
    for name, provider_dict in gateway_dict["providers"].items():
        if provider_dict["kind"] == "anthropic":
            key = os.environ.get(provider_dict["api_key_env"], "") or settings.anthropic_api_key
            if key:
                adapters_dict[name] = AnthropicAdapter(key)
            continue
        base_url = provider_dict.get("base_url") or settings.selfhosted_base_url
        api_key = ""
        if provider_dict.get("api_key_env"):
            api_key = os.environ.get(provider_dict["api_key_env"], "") or (
                settings.groq_api_key if name == "groq" else ""
            )
            if not api_key:
                continue
        adapters_dict[name] = OpenAICompatibleAdapter(name, base_url, api_key)
    return adapters_dict


def build_store(settings: Settings) -> GatewayStore:
    """Return a Redis store when REDIS_URL is set, else an in-memory store."""
    if settings.redis_url:
        return RedisStore.from_url(settings.redis_url)
    logger.warning("REDIS_URL not set; budgets, rate limits, and the exact cache are per-process")
    return MemoryStore()


def build_service(settings: Settings | None = None, o_record: bool | None = None) -> GatewayService:
    """
    Assemble the gateway from configs and environment.

    Parameters
    ----------
    settings : Settings or None
        Process settings.
    o_record : bool or None
        Record real responses for demo replay; defaults to ROI_RECORD_LLM=1.
    """
    settings = settings or get_settings()
    gateway_dict = load_yaml(config_path(GATEWAY_FILE))
    store = build_store(settings)
    try:
        routing_table = RoutingTable.load()
    except RoutingTableMissingError as exc:
        logger.warning(str(exc))
        routing_table = None
    semantic_cache = None
    cache_dict = gateway_dict["cache"]
    if settings.redis_url:
        semantic_cache = RedisSemanticCache(
            settings.redis_url,
            cache_dict["semantic_distance_threshold"],
            cache_dict["semantic_ttl_s"],
            fastembed_vectorizer(cache_dict["embedding_model"], EMBEDDING_CACHE_DIR),
        )
    breaker_dict = gateway_dict["breaker"]
    limits_dict = {
        name: RateLimit(float(v["requests_per_minute"]), float(v["tokens_per_minute"]))
        for name, v in gateway_dict["rate_limits"].items()
        if isinstance(v, dict)
    }
    if o_record is None:
        o_record = os.environ.get("ROI_RECORD_LLM") == "1"
    calls_dict = gateway_dict["calls"]
    deps = GatewayDeps(
        adapters_dict=build_adapters(settings, gateway_dict),
        prices=PriceTable.load(),
        budgets=BudgetTracker(BudgetConfig.load(), store),
        breakers=BreakerRegistry(
            lambda: CircuitBreaker(
                breaker_dict["failure_threshold"],
                breaker_dict["window_s"],
                breaker_dict["half_open_after_s"],
            )
        ),
        rate_limiter=WorkflowRateLimiter(
            store, limits_dict, gateway_dict["rate_limits"]["max_wait_s"]
        ),
        exact_cache=ExactCache(store, cache_dict["exact_ttl_s"]),
        step_classes_dict=load_step_classes().step_classes,
        routing_table=routing_table,
        semantic_cache=semantic_cache,
        recorder=Recorder(RECORDINGS_DIR / RECORDINGS_FILE) if o_record else None,
        settings=CallSettings(
            timeout_s=calls_dict["timeout_s"],
            retries=calls_dict["retries"],
            backoff_base_s=calls_dict["backoff_base_s"],
            backoff_max_s=calls_dict["backoff_max_s"],
            min_step_success=gateway_dict["routing"]["min_step_success"],
            routed_model=gateway_dict["routing"]["routed_model"],
            o_strict=settings.gateway_strict,
        ),
    )
    return GatewayService(deps)


# ============= App =============


def create_app(service: GatewayService | None = None) -> FastAPI:
    """
    Create the gateway app.

    Parameters
    ----------
    service : GatewayService or None
        Injected service for tests; built from configs on startup otherwise.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        init_db()
        setup_tracing(f"{settings.service_name}-gateway", settings.otel_exporter_otlp_endpoint)
        app.state.service = service or build_service(settings)
        yield

    app = FastAPI(title="Agent ROI Console Gateway", version="2.0.0", lifespan=lifespan)

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest, request: Request):
        """OpenAI-compatible chat completions with routing and attribution."""
        settings = get_settings()
        if settings.gateway_api_key:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {settings.gateway_api_key}":
                return JSONResponse(
                    {"error": {"type": "unauthorized", "message": "bad key"}}, status_code=401
                )
        try:
            response_dict = await request.app.state.service.complete(body)
        except GatewayError as exc:
            return JSONResponse(exc.to_dict(), status_code=exc.status)
        if not body.stream:
            return JSONResponse(response_dict)

        async def events() -> AsyncIterator[str]:
            for chunk in stream_chunks(response_dict):
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/v1/models")
    async def list_models(request: Request) -> dict[str, Any]:
        """List priced models plus the routed pseudo-model."""
        deps = request.app.state.service.deps
        data_list = [
            {"id": deps.settings.routed_model, "object": "model", "owned_by": "roiconsole"}
        ]
        data_list += [
            {"id": m, "object": "model", "owned_by": deps.prices.get(m).provider}
            for m in deps.prices.models()
        ]
        return {"object": "list", "data": data_list}

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        """Liveness plus routing-table and breaker status."""
        deps = request.app.state.service.deps
        return {
            "status": "ok",
            "routing_table": deps.routing_table.metadata_dict if deps.routing_table else None,
            "providers": sorted(deps.adapters_dict),
            "unpriced_models": deps.prices.unpriced_dict,
            "breakers": deps.breakers.states(),
        }

    @app.get("/metrics")
    async def prometheus_metrics() -> PlainTextResponse:
        """Prometheus exposition."""
        return PlainTextResponse(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()


def main() -> None:
    """Run the gateway with uvicorn."""
    import uvicorn

    configure_logging("gateway")
    uvicorn.run(
        app,
        host=os.environ.get("GATEWAY_HOST", "127.0.0.1"),
        port=int(os.environ.get("GATEWAY_PORT", "8090")),
    )


if __name__ == "__main__":
    main()
