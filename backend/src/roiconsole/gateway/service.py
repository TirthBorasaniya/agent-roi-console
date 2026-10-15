"""Gateway core: routing, budgets, rate limits, provider fallback, caching, and attribution."""

# ============= Standard Library =============
import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ============= Local =============
from roiconsole.common import metrics
from roiconsole.common.db import insert_row, session, utc_now
from roiconsole.gateway.adapters.base import (
    Adapter,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from roiconsole.gateway.adapters.replay import Recorder
from roiconsole.gateway.breaker import STATE_GAUGE_DICT, BreakerRegistry
from roiconsole.gateway.budgets import BudgetTracker
from roiconsole.gateway.cache import ExactCache, SemanticLookup, exact_key, prompt_text
from roiconsole.gateway.pricing import MissingPriceError, PriceTable
from roiconsole.gateway.ratelimit import RateLimitExceededError, WorkflowRateLimiter
from roiconsole.gateway.router import RoutingTable, plan_route
from roiconsole.gateway.schemas import METADATA_FIELDS, ChatCompletionRequest, completion_response
from roiconsole.gateway.telemetry import chat_span_attributes, tracer
from roiconsole.workflows.schema import StepClass

# ============= Constants =============
logger = logging.getLogger(__name__)
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


# ============= Types =============


class GatewayError(RuntimeError):
    """
    A request the gateway could not serve.

    Parameters
    ----------
    status : int
        HTTP status for the API layer.
    code : str
        Machine-readable error type, e.g. budget_exhausted.
    message : str
        Description.
    detail_dict : dict
        Attempts and other context returned to the client.
    """

    def __init__(
        self, status: int, code: str, message: str, detail_dict: dict | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail_dict = detail_dict or {}

    def to_dict(self) -> dict[str, Any]:
        """Return an OpenAI-style error body."""
        return {"error": {"type": self.code, "message": str(self), **self.detail_dict}}


@dataclass
class CallSettings:
    """Timeout, retry, and routing settings from configs/gateway.yaml."""

    timeout_s: float = 30.0
    retries: int = 2
    backoff_base_s: float = 0.5
    backoff_max_s: float = 4.0
    min_step_success: float = 0.9
    routed_model: str = "auto"
    o_strict: bool = True


@dataclass
class Attempt:
    """One provider attempt or skip, reported in x_roi.attempts."""

    model: str
    outcome: str
    latency_s: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class GatewayDeps:
    """Everything the service needs, injected so tests can use fakes."""

    adapters_dict: dict[str, Adapter]
    prices: PriceTable
    budgets: BudgetTracker
    breakers: BreakerRegistry
    rate_limiter: WorkflowRateLimiter
    exact_cache: ExactCache
    step_classes_dict: dict[str, StepClass]
    routing_table: RoutingTable | None = None
    semantic_cache: SemanticLookup | None = None
    recorder: Recorder | None = None
    settings: CallSettings = field(default_factory=CallSettings)
    o_persist: bool = True


# ============= Helpers =============


def estimate_prompt_tokens(message_list: list[dict[str, Any]], tool_list: list | None) -> int:
    """Estimate prompt tokens at four characters per token plus per-message overhead."""
    text = json.dumps(message_list) + (json.dumps(tool_list) if tool_list else "")
    return len(text) // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS * len(message_list)


def backoff_s(attempt_index: int, base_s: float, max_s: float) -> float:
    """Full-jitter exponential backoff."""
    return random.uniform(0, min(max_s, base_s * (2**attempt_index)))


# ============= Service =============


class GatewayService:
    """
    Serves chat completions with routing, budgets, fallback, caching, and attribution.

    Parameters
    ----------
    deps : GatewayDeps
        Adapters, price table, budgets, breakers, limiter, caches, and settings.
    """

    def __init__(self, deps: GatewayDeps) -> None:
        self.deps = deps

    # ============= Validation and Planning =============

    def _metadata(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Validate attribution metadata; strict mode rejects requests without it."""
        metadata_dict = dict(request.metadata or {})
        missing_list = [f for f in METADATA_FIELDS if not metadata_dict.get(f)]
        if missing_list and self.deps.settings.o_strict:
            raise GatewayError(400, "missing_metadata", f"metadata must include {missing_list}")
        for name in missing_list:
            metadata_dict[name] = "unknown"
        metadata_dict.setdefault("cache", "on")
        return metadata_dict

    def _step_class(self, name: str) -> StepClass | None:
        """Return the step class definition, if known."""
        return self.deps.step_classes_dict.get(name)

    def _plan(
        self, request: ChatCompletionRequest, metadata_dict: dict[str, Any]
    ) -> tuple[list[str], bool]:
        """Return the ordered models to try and whether the plan is below the success bar."""
        if request.model != self.deps.settings.routed_model:
            if not self.deps.prices.has(request.model):
                raise GatewayError(400, "unknown_model", f"model {request.model} has no price")
            return [request.model], False
        if self.deps.routing_table is None:
            raise GatewayError(
                503, "routing_table_missing", "routing table not generated; run make eval-steps"
            )
        step_class = metadata_dict["step_class"]
        definition = self._step_class(step_class)
        min_success = (
            definition.min_step_success if definition else self.deps.settings.min_step_success
        )
        try:
            plan = plan_route(self.deps.routing_table, step_class, min_success)
        except KeyError as exc:
            raise GatewayError(400, "unknown_step_class", str(exc)) from exc
        priced_list = [m for m in plan.models if self.deps.prices.has(m)]
        if not priced_list:
            raise GatewayError(503, "no_priced_candidate", f"no priced candidate for {step_class}")
        return priced_list, plan.o_below_bar

    async def _fit_budget(
        self, model_list: list[str], metadata_dict: dict, prompt_tokens: int, max_tokens: int
    ) -> tuple[list[str], bool, float]:
        """
        Downgrade when the remaining run budget cannot cover the chosen model.

        Returns
        -------
        models : list of str
            Plan to execute.
        o_downgraded : bool
            True when the first choice was replaced by a cheaper qualifying model.
        remaining : float
            Remaining budget before the call.
        """
        remaining = await self.deps.budgets.remaining(
            metadata_dict["workflow_id"], metadata_dict["run_id"]
        )
        estimate_dict = {
            m: self.deps.prices.estimate(m, prompt_tokens, max_tokens) for m in model_list
        }
        if estimate_dict[model_list[0]] <= remaining:
            return model_list, False, remaining
        fitting_list = sorted(
            (m for m in model_list if estimate_dict[m] <= remaining), key=estimate_dict.get
        )
        if not fitting_list:
            metrics.BUDGET_HALTS_TOTAL.labels(workflow=metadata_dict["workflow_id"]).inc()
            raise GatewayError(
                402,
                "budget_exhausted",
                f"remaining budget {remaining:.6f} USD cannot cover any qualifying candidate",
                {"remaining_usd": remaining, "estimates_usd": estimate_dict},
            )
        return fitting_list, True, remaining

    # ============= Provider Calls =============

    async def _call_model(
        self, model: str, provider_request: ProviderRequest, attempt_list: list[Attempt]
    ) -> ProviderResponse | None:
        """Call one model with timeout, jittered retries, and its circuit breaker."""
        settings = self.deps.settings
        breaker = self.deps.breakers.get(model)
        adapter = self.deps.adapters_dict.get(self.deps.prices.get(model).provider)
        if adapter is None:
            attempt_list.append(Attempt(model, "no_adapter", error="provider not configured"))
            return None
        for try_index in range(settings.retries + 1):
            if not breaker.allow():
                attempt_list.append(Attempt(model, "circuit_open"))
                metrics.CIRCUIT_STATE.labels(model=model).set(STATE_GAUGE_DICT[breaker.state])
                return None
            started = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    adapter.complete(provider_request), settings.timeout_s
                )
            except TimeoutError:
                error = ProviderError("gateway timeout", None, True, "timeout")
            except ProviderError as exc:
                error = exc
            else:
                breaker.record_success()
                metrics.CIRCUIT_STATE.labels(model=model).set(0.0)
                metrics.LLM_LATENCY_SECONDS.labels(model=model).observe(time.monotonic() - started)
                attempt_list.append(Attempt(model, "ok", round(time.monotonic() - started, 4)))
                return response
            breaker.record_failure()
            metrics.CIRCUIT_STATE.labels(model=model).set(STATE_GAUGE_DICT[breaker.state])
            metrics.LLM_CALLS_TOTAL.labels(model=model, outcome=error.kind).inc()
            attempt_list.append(
                Attempt(model, error.kind, round(time.monotonic() - started, 4), str(error)[:300])
            )
            if not error.o_retryable:
                return None
            if try_index < settings.retries:
                await asyncio.sleep(
                    backoff_s(try_index, settings.backoff_base_s, settings.backoff_max_s)
                )
        return None

    # ============= Entry Point =============

    async def complete(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """
        Serve one chat completion.

        Returns
        -------
        response : dict
            OpenAI chat.completion with an `x_roi` object: model, provider, cost_usd,
            cache, fallback_depth, downgraded, below_bar, attempts.

        Raises
        ------
        GatewayError
            400 bad request or metadata, 402 budget exhausted, 429 rate limited,
            502 every candidate failed, 503 routing table missing.
        """
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        metadata_dict = self._metadata(request)
        step_class = metadata_dict["step_class"]
        max_tokens = request.resolved_max_tokens
        prompt_tokens = estimate_prompt_tokens(request.messages, request.tools)
        model_list, o_below_bar = self._plan(request, metadata_dict)
        try:
            await self.deps.rate_limiter.acquire(
                metadata_dict["workflow_id"], prompt_tokens + max_tokens
            )
        except RateLimitExceededError as exc:
            self._persist(
                request_id,
                metadata_dict,
                model_list[0],
                "",
                0,
                0,
                0.0,
                "miss",
                0,
                "rate_limited",
                str(exc),
                started,
            )
            raise GatewayError(429, "rate_limited", str(exc)) from exc

        o_cache = metadata_dict.get("cache") == "on"
        definition = self._step_class(step_class)
        o_semantic = bool(
            o_cache and definition and definition.cacheable and self.deps.semantic_cache
        )
        cache_key = exact_key(request.model, request.generation_params())
        if o_cache:
            cached = await self.deps.exact_cache.get(cache_key)
            if cached is None and o_semantic:
                cached = await self.deps.semantic_cache.get(
                    prompt_text(request.messages), step_class, request.model
                )
                cache_kind = "semantic"
            else:
                cache_kind = "exact"
            if cached is not None:
                return self._serve_cached(request_id, cached, cache_kind, metadata_dict, started)

        try:
            model_list, o_downgraded, remaining = await self._fit_budget(
                model_list, metadata_dict, prompt_tokens, max_tokens
            )
        except GatewayError as exc:
            self._persist(
                request_id,
                metadata_dict,
                model_list[0],
                "",
                0,
                0,
                0.0,
                "miss",
                0,
                "budget_exhausted",
                str(exc),
                started,
            )
            raise
        attempt_list: list[Attempt] = []
        response: ProviderResponse | None = None
        chosen = model_list[0]
        depth = 0
        for depth, model in enumerate(model_list):
            if depth > 0:
                remaining = await self.deps.budgets.remaining(
                    metadata_dict["workflow_id"], metadata_dict["run_id"]
                )
                if self.deps.prices.estimate(model, prompt_tokens, max_tokens) > remaining:
                    attempt_list.append(Attempt(model, "budget_skip"))
                    continue
            provider_request = ProviderRequest(
                provider_model=self.deps.prices.get(model).provider_model,
                messages=request.messages,
                tools=request.tools,
                tool_choice=request.tool_choice,
                max_tokens=max_tokens,
                temperature=request.temperature,
                response_format=request.response_format,
            )
            response = await self._call_model(model, provider_request, attempt_list)
            if response is not None:
                chosen = model
                break
        if response is None:
            detail_dict = {"attempts": [a.to_dict() for a in attempt_list]}
            self._persist(
                request_id,
                metadata_dict,
                model_list[-1],
                "",
                0,
                0,
                0.0,
                "miss",
                depth,
                "error",
                "all candidates failed",
                started,
            )
            raise GatewayError(502, "all_providers_failed", "every candidate failed", detail_dict)

        price = self.deps.prices.get(chosen)
        cost_usd = self.deps.prices.actual(chosen, response.input_tokens, response.output_tokens)
        await self.deps.budgets.charge(
            metadata_dict["workflow_id"], metadata_dict["run_id"], cost_usd
        )
        if self.deps.recorder is not None:
            self.deps.recorder.record(chosen, provider_request, response)
        roi_dict = {
            "request_id": request_id,
            "model": chosen,
            "provider": price.provider,
            "cost_usd": cost_usd,
            "cache": "miss",
            "fallback_depth": depth,
            "downgraded": o_downgraded,
            "below_bar": o_below_bar,
            "budget_remaining_usd": remaining - cost_usd,
            "attempts": [a.to_dict() for a in attempt_list],
        }
        response_dict = completion_response(
            chosen,
            response.content,
            response.tool_calls,
            response.finish_reason,
            response.input_tokens,
            response.output_tokens,
            roi_dict,
        )
        if o_cache:
            await self.deps.exact_cache.put(cache_key, response_dict)
            if o_semantic and not response.tool_calls:
                await self.deps.semantic_cache.put(
                    prompt_text(request.messages), step_class, request.model, response_dict
                )
        metrics.LLM_CALLS_TOTAL.labels(model=chosen, outcome="ok").inc()
        metrics.LLM_COST_USD_TOTAL.labels(workflow=metadata_dict["workflow_id"], model=chosen).inc(
            cost_usd
        )
        self._span(
            chosen,
            price.provider,
            response.response_model or chosen,
            response.input_tokens,
            response.output_tokens,
            metadata_dict,
            cost_usd,
            "miss",
            depth,
        )
        self._persist(
            request_id,
            metadata_dict,
            chosen,
            price.provider,
            response.input_tokens,
            response.output_tokens,
            cost_usd,
            "miss",
            depth,
            "ok",
            None,
            started,
        )
        return response_dict

    def _serve_cached(
        self,
        request_id: str,
        cached: dict[str, Any],
        cache_kind: str,
        metadata_dict: dict,
        started: float,
    ) -> dict[str, Any]:
        """Return a cached response at zero cost, recording the hit."""
        model = cached.get("x_roi", {}).get("model", cached.get("model", ""))
        provider = cached.get("x_roi", {}).get("provider", "")
        roi_dict = {
            **cached.get("x_roi", {}),
            "request_id": request_id,
            "cost_usd": 0.0,
            "cache": cache_kind,
            "fallback_depth": 0,
            "attempts": [],
        }
        response_dict = {**cached, "id": f"chatcmpl-{request_id[:24]}", "x_roi": roi_dict}
        usage = cached.get("usage", {})
        metrics.CACHE_HITS_TOTAL.labels(kind=cache_kind).inc()
        metrics.LLM_CALLS_TOTAL.labels(model=model, outcome=f"cache_{cache_kind}").inc()
        self._span(model, provider, model, 0, 0, metadata_dict, 0.0, cache_kind, 0)
        self._persist(
            request_id,
            metadata_dict,
            model,
            provider,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
            0.0,
            cache_kind,
            0,
            "ok",
            None,
            started,
        )
        return response_dict

    # ============= Recording =============

    def _span(
        self,
        model: str,
        provider: str,
        response_model: str,
        input_tokens: int,
        output_tokens: int,
        metadata_dict: dict,
        cost_usd: float,
        cache_kind: str,
        depth: int,
    ) -> None:
        """Emit the `chat {model}` span."""
        attributes = chat_span_attributes(
            model,
            provider,
            response_model,
            input_tokens,
            output_tokens,
            metadata_dict,
            cost_usd,
            cache_kind,
            depth,
        )
        with tracer().start_as_current_span(f"chat {model}", attributes=attributes):
            pass

    def _persist(
        self,
        request_id: str,
        metadata_dict: dict,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cache_kind: str,
        depth: int,
        outcome: str,
        error: str | None,
        started: float,
    ) -> None:
        """Write one llm_calls row for cost attribution per workflow, run, step, and model."""
        if not self.deps.o_persist:
            return
        row_dict = {
            "request_id": request_id,
            "workflow_id": metadata_dict.get("workflow_id"),
            "run_id": metadata_dict.get("run_id"),
            "step_id": metadata_dict.get("step_id"),
            "step_class": metadata_dict.get("step_class"),
            "model": model,
            "provider": provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "cache": cache_kind,
            "fallback_depth": depth,
            "outcome": outcome,
            "error": error,
            "latency_s": round(time.monotonic() - started, 4),
            "created_at": utc_now(),
        }
        with session() as conn:
            insert_row(conn, "llm_calls", row_dict)


def missing_price_models(table: RoutingTable, prices: PriceTable) -> list[str]:
    """Return routing-table models that have no price (must be empty, SPEC 5.4)."""
    missing_list = []
    for model in table.models():
        try:
            prices.get(model)
        except MissingPriceError:
            missing_list.append(model)
    return missing_list
