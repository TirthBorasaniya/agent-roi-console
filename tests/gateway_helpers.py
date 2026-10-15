"""Builders for gateway services wired to fake providers and in-memory stores."""

# ============= Standard Library =============
from pathlib import Path

# ============= Third-Party =============
import yaml

# ============= Local =============
from roiconsole.gateway.adapters.fake import FakeAdapter
from roiconsole.gateway.adapters.fault import FaultInjectingAdapter
from roiconsole.gateway.breaker import BreakerRegistry, CircuitBreaker
from roiconsole.gateway.budgets import BudgetConfig, BudgetTracker, WorkflowBudget
from roiconsole.gateway.cache import ExactCache
from roiconsole.gateway.pricing import ModelPrice, PriceTable
from roiconsole.gateway.ratelimit import RateLimit, WorkflowRateLimiter
from roiconsole.gateway.router import RoutingTable
from roiconsole.gateway.service import CallSettings, GatewayDeps, GatewayService
from roiconsole.gateway.store import MemoryStore
from roiconsole.workflows.registry import load_step_classes

# ============= Constants =============
CHEAP, MID, PRICEY = "fake/cheap", "fake/mid", "fake/pricey"
PRICES = {CHEAP: (0.1, 0.4), MID: (1.0, 4.0), PRICEY: (10.0, 40.0)}


# ============= Builders =============


def price_table() -> PriceTable:
    """Three fake models served by three fake providers."""
    prices_dict = {
        model: ModelPrice(model, model.replace("/", "_"), model, inp, out, "test", "2026-10-14")
        for model, (inp, out) in PRICES.items()
    }
    return PriceTable(prices_dict, {})


def routing_table(tmp_path: Path, success_dict: dict[str, float] | None = None) -> RoutingTable:
    """Write and load a routing table for step class 'extraction' over the fake models."""
    success_dict = success_dict or {CHEAP: 0.95, MID: 0.97, PRICEY: 0.99}
    data = {
        "version": 1,
        "generated_at": "2026-10-14T00:00:00Z",
        "evaluation_date": "2026-10-14",
        "step_classes": {
            step_class: {
                "candidates": [
                    {
                        "model": m,
                        "step_success": s,
                        "n_trials": 20,
                        "mean_cost_usd": PRICES[m][1] / 1e4,
                    }
                    for m, s in success_dict.items()
                ]
            }
            for step_class in ("extraction", "summarization")
        },
    }
    path = tmp_path / "routing_table.yaml"
    path.write_text(yaml.safe_dump(data))
    return RoutingTable.load(path)


def build_test_service(
    tmp_path: Path,
    per_run_usd: float = 1.0,
    success_dict: dict[str, float] | None = None,
    clock=None,
    timeout_s: float = 0.5,
    rpm: float = 6000,
) -> tuple[GatewayService, dict[str, FaultInjectingAdapter]]:
    """Return a service over fault-injectable fake providers and the adapters by model."""
    adapters_dict = {
        model.replace("/", "_"): FaultInjectingAdapter(FakeAdapter(model.replace("/", "_")))
        for model in PRICES
    }
    store = MemoryStore()
    breaker_kwargs = {"clock": clock} if clock else {}
    deps = GatewayDeps(
        adapters_dict=adapters_dict,
        prices=price_table(),
        budgets=BudgetTracker(BudgetConfig(WorkflowBudget(per_run_usd, 100.0), {}), store),
        breakers=BreakerRegistry(lambda: CircuitBreaker(**breaker_kwargs)),
        rate_limiter=WorkflowRateLimiter(store, {"default": RateLimit(rpm, 10_000_000)}, 0.5),
        exact_cache=ExactCache(store, 3600),
        step_classes_dict=load_step_classes().step_classes,
        routing_table=routing_table(tmp_path, success_dict),
        settings=CallSettings(
            timeout_s=timeout_s, retries=2, backoff_base_s=0.0, backoff_max_s=0.0
        ),
        o_persist=True,
    )
    by_model = {model: adapters_dict[model.replace("/", "_")] for model in PRICES}
    return GatewayService(deps), by_model
