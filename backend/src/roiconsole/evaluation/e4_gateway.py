"""E4: gateway fallback under injected faults, budget enforcement, and cache hit rates."""

# ============= Standard Library =============
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

# ============= Third-Party =============
import yaml

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.common.settings import REPO_ROOT, Settings, get_settings
from roiconsole.evaluation.stats import distribution, mean, percentile, rate_summary
from roiconsole.evaluation.task_data import KB_TAGS
from roiconsole.gateway.adapters.base import Adapter
from roiconsole.gateway.adapters.fake import FakeAdapter
from roiconsole.gateway.adapters.fault import FaultInjectingAdapter, FaultPlan
from roiconsole.gateway.app import build_adapters
from roiconsole.gateway.breaker import BreakerRegistry, CircuitBreaker
from roiconsole.gateway.budgets import BudgetConfig, BudgetTracker, WorkflowBudget
from roiconsole.gateway.cache import ExactCache, RedisSemanticCache, fastembed_vectorizer
from roiconsole.gateway.pricing import ModelPrice, PriceTable
from roiconsole.gateway.ratelimit import RateLimit, WorkflowRateLimiter
from roiconsole.gateway.router import RoutingTable
from roiconsole.gateway.schemas import ChatCompletionRequest
from roiconsole.gateway.service import CallSettings, GatewayDeps, GatewayError, GatewayService
from roiconsole.gateway.store import MemoryStore, RedisStore
from roiconsole.workflows.registry import load_step_classes

# ============= Constants =============
logger = logging.getLogger(__name__)
E4_CONFIG = "eval/e4.yaml"
WORKFLOW_ID = "e4_gateway"
FAKE_PRICES = {
    "fake/primary": (0.10, 0.40),
    "fake/secondary": (0.50, 2.00),
    "fake/tertiary": (1.00, 4.00),
}
EXTRACTION_PROMPT = (
    "Choose the single tag from this list that best matches the question.\n"
    "Tags: {tags}\nQuestion: {question}\n"
    'Respond with JSON of the form {{"tag": "<one tag from the list>"}}.'
)


# ============= Construction =============


def write_routing_table(
    path: Path, model_list: list[str], step_class: str, prices: PriceTable
) -> RoutingTable:
    """
    Write an E4-only routing table that keeps `model_list` in the given order.

    E4 tests the mechanism (fallback order), not model quality, so every
    candidate is marked as meeting the bar and mean cost is the configured
    order. The table never replaces configs/routing_table.yaml.
    """
    candidate_list = [
        {"model": m, "step_success": 1.0, "n_trials": 0, "mean_cost_usd": float(i)}
        for i, m in enumerate(model_list)
    ]
    data = {
        "version": 1,
        "generated_at": "e4-synthetic",
        "note": "E4 mechanism test only",
        "step_classes": {step_class: {"candidates": candidate_list}},
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return RoutingTable.load(path)


def fake_price_table() -> PriceTable:
    """Exact prices for the fake providers used by the budget scenarios."""
    return PriceTable(
        {
            m: ModelPrice(m, m.replace("/", "_"), m, inp, out, "e4-fake", "e4")
            for m, (inp, out) in FAKE_PRICES.items()
        },
        {},
    )


def build_e4_service(
    adapters_dict: dict[str, Adapter],
    prices: PriceTable,
    table: RoutingTable,
    per_run_usd: float,
    store: Any,
    semantic_cache: Any = None,
    timeout_s: float = 30.0,
) -> GatewayService:
    """Assemble a gateway service with fresh breakers for one scenario."""
    deps = GatewayDeps(
        adapters_dict=adapters_dict,
        prices=prices,
        budgets=BudgetTracker(BudgetConfig(WorkflowBudget(per_run_usd, 1e9), {}), store),
        breakers=BreakerRegistry(CircuitBreaker),
        rate_limiter=WorkflowRateLimiter(store, {"default": RateLimit(600, 1_000_000)}, 60.0),
        exact_cache=ExactCache(store, 3600),
        step_classes_dict=load_step_classes().step_classes,
        routing_table=table,
        semantic_cache=semantic_cache,
        settings=CallSettings(timeout_s=timeout_s),
        o_persist=False,
    )
    return GatewayService(deps)


def extraction_request(
    question: str, run_id: str, max_tokens: int, o_cache: bool
) -> ChatCompletionRequest:
    """Build the routed extraction request used across E4."""
    prompt = EXTRACTION_PROMPT.format(tags=", ".join(KB_TAGS), question=question)
    return ChatCompletionRequest(
        model="auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        metadata={
            "workflow_id": WORKFLOW_ID,
            "run_id": run_id,
            "step_id": "extract_filter",
            "step_class": "extraction",
            "cache": "on" if o_cache else "off",
        },
    )


# ============= Fallback =============


async def run_fallback(config_dict: dict, settings: Settings, tmp_dir: Path) -> dict[str, Any]:
    """
    Inject each fault scenario on the primary model and measure fallback.

    Returns
    -------
    results : dict
        Per scenario: success rate with Wilson interval, fallback depth
        distribution, latency percentiles, latency added over baseline,
        injected fault count, and circuit-open skips.
    """
    fallback_dict = config_dict["fallback"]
    model_list = config_dict["models"]
    if config_dict["provider_mode"] == "real":
        prices = PriceTable.load()
        base_adapters = build_adapters(settings, load_yaml(config_path("gateway.yaml")))
    else:
        prices = fake_price_table()
        model_list = list(FAKE_PRICES)
        base_adapters = {
            m.replace("/", "_"): FakeAdapter(m.replace("/", "_"), latency_s=0.05)
            for m in model_list
        }
    missing_list = [m for m in model_list if prices.get(m).provider not in base_adapters]
    if missing_list:
        raise RuntimeError(f"no adapter configured for {missing_list}; set the provider keys")
    table = write_routing_table(
        tmp_dir / "e4_routing.yaml", model_list, config_dict["step_class"], prices
    )
    primary = model_list[0]
    primary_provider_model = prices.get(primary).provider_model
    question_list = [
        g["base"] for g in load_yaml(REPO_ROOT / config_dict["cache"]["query_set"])["groups"]
    ]
    scenario_results_dict: dict[str, Any] = {}
    baseline_p50 = None
    for scenario in fallback_dict["scenarios"]:
        fault_adapters = {
            name: FaultInjectingAdapter(adapter) for name, adapter in base_adapters.items()
        }
        plan = FaultPlan(
            scenario["kind"], scenario["rate"], seed=7, provider_models=(primary_provider_model,)
        )
        fault_adapters[prices.get(primary).provider].set_plan(plan)
        service = build_e4_service(fault_adapters, prices, table, 1e9, MemoryStore())
        latency_list, depth_list, outcome_list = [], [], []
        circuit_skips = 0
        for i in range(fallback_dict["requests_per_scenario"]):
            request = extraction_request(
                question_list[i % len(question_list)],
                f"e4-{scenario['name']}-{i}",
                fallback_dict["max_tokens"],
                False,
            )
            started = time.monotonic()
            try:
                response = await service.complete(request)
            except GatewayError as exc:
                outcome_list.append(exc.code)
                latency_list.append(time.monotonic() - started)
                continue
            latency_list.append(time.monotonic() - started)
            outcome_list.append("ok")
            depth_list.append(response["x_roi"]["fallback_depth"])
            circuit_skips += sum(
                a["outcome"] == "circuit_open" for a in response["x_roi"]["attempts"]
            )
        successes = outcome_list.count("ok")
        p50 = percentile(latency_list, 0.5)
        if scenario["name"] == "baseline":
            baseline_p50 = p50
        scenario_results_dict[scenario["name"]] = {
            "fault": scenario,
            "success": rate_summary(successes, len(outcome_list)),
            "outcomes": distribution(outcome_list),
            "fallback_depth_distribution": distribution(depth_list),
            "mean_fallback_depth": mean([float(d) for d in depth_list]),
            "latency_p50_s": p50,
            "latency_p95_s": percentile(latency_list, 0.95),
            "added_latency_p50_s": (p50 - baseline_p50)
            if (p50 is not None and baseline_p50 is not None)
            else None,
            "faults_injected": fault_adapters[prices.get(primary).provider].injected,
            "circuit_open_skips": circuit_skips,
        }
    fault_names = [s["name"] for s in fallback_dict["scenarios"] if s["kind"] != "none"]
    total_ok = sum(scenario_results_dict[n]["success"]["successes"] for n in fault_names)
    total = sum(scenario_results_dict[n]["success"]["trials"] for n in fault_names)
    return {
        "models": model_list,
        "primary": primary,
        "scenarios": scenario_results_dict,
        "fallback_success_under_faults": rate_summary(total_ok, total),
    }


# ============= Budget =============


async def run_budget(config_dict: dict, tmp_dir: Path) -> dict[str, Any]:
    """
    Verify budget enforcement: no run overspends, and exhaustion halts with budget_exhausted.

    Each per-run budget gets one run of repeated requests against fake providers
    with exact prices. A run is correct when total spend never exceeds the budget
    and the run ends in budget_exhausted rather than overspending.
    """
    budget_dict = config_dict["budget"]
    prices = fake_price_table()
    model_list = list(FAKE_PRICES)
    table = write_routing_table(
        tmp_dir / "e4_budget_routing.yaml", model_list, config_dict["step_class"], prices
    )
    run_list = []
    for per_run_usd in budget_dict["per_run_usd_list"]:
        adapters = {m.replace("/", "_"): FakeAdapter(m.replace("/", "_")) for m in model_list}
        service = build_e4_service(adapters, prices, table, per_run_usd, MemoryStore())
        run_id = f"e4-budget-{per_run_usd}"
        outcome_list = []
        for i in range(budget_dict["requests_per_run"]):
            request = extraction_request(
                f"budget probe {i}", run_id, budget_dict["max_tokens"], False
            )
            try:
                await service.complete(request)
                outcome_list.append("ok")
            except GatewayError as exc:
                outcome_list.append(exc.code)
                if exc.code == "budget_exhausted":
                    break
        spent, _ = await service.deps.budgets.spent(WORKFLOW_ID, run_id)
        # correct: never above budget, and the run either halted on budget_exhausted
        # or was fully served because the budget was never reached
        o_halted = outcome_list[-1] == "budget_exhausted"
        o_fully_served = outcome_list.count("ok") == budget_dict["requests_per_run"]
        o_correct = spent <= per_run_usd + 1e-12 and (o_halted or o_fully_served)
        run_list.append(
            {
                "per_run_usd": per_run_usd,
                "spent_usd": spent,
                "requests_served": outcome_list.count("ok"),
                "final_outcome": outcome_list[-1],
                "o_halted": o_halted,
                "o_correct": o_correct,
            }
        )
    return {
        "runs": run_list,
        "enforcement_correct": rate_summary(sum(r["o_correct"] for r in run_list), len(run_list)),
        "max_overspend_usd": max(r["spent_usd"] - r["per_run_usd"] for r in run_list),
    }


# ============= Cache =============


async def run_cache(config_dict: dict, settings: Settings, tmp_dir: Path) -> dict[str, Any]:
    """
    Measure exact and semantic hit rates and the semantic wrong-hit rate.

    Sequence per group: the base query (miss, stored), exact repeats (expected
    exact hits), paraphrases with the same answer (semantic hits are correct),
    then near misses with a different answer (any semantic hit is wrong).
    """
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is required: the semantic cache needs Redis 8")
    cache_dict = config_dict["cache"]
    gateway_dict = load_yaml(config_path("gateway.yaml"))["cache"]
    query_dict = load_yaml(REPO_ROOT / cache_dict["query_set"])
    if config_dict["provider_mode"] == "real":
        prices = PriceTable.load()
        adapters = build_adapters(settings, load_yaml(config_path("gateway.yaml")))
        model_list = config_dict["models"]
    else:
        prices = fake_price_table()
        model_list = list(FAKE_PRICES)
        adapters = {m.replace("/", "_"): FakeAdapter(m.replace("/", "_")) for m in model_list}
    table = write_routing_table(
        tmp_dir / "e4_cache_routing.yaml", model_list, config_dict["step_class"], prices
    )
    index_name = f"roiconsole_e4_{uuid.uuid4().hex[:8]}"
    semantic = RedisSemanticCache(
        settings.redis_url,
        gateway_dict["semantic_distance_threshold"],
        3600,
        fastembed_vectorizer(gateway_dict["embedding_model"], REPO_ROOT / ".cache" / "fastembed"),
        name=index_name,
    )
    store = RedisStore.from_url(settings.redis_url, prefix=f"{index_name}:")
    service = build_e4_service(adapters, prices, table, 1e9, store, semantic_cache=semantic)
    answer_by_content: dict[str, str] = {}
    counts_dict = {
        "exact_repeat_hits": 0,
        "exact_repeats": 0,
        "paraphrase_semantic_hits": 0,
        "paraphrases": 0,
        "near_miss_semantic_hits": 0,
        "near_misses": 0,
        "semantic_hits": 0,
        "semantic_wrong_hits": 0,
    }

    async def ask(question: str, expected: str, run_id: str) -> str:
        """Send one query and update hit and wrong-hit counts."""
        request = extraction_request(question, run_id, cache_dict["max_tokens"], True)
        response = await service.complete(request)
        content = response["choices"][0]["message"]["content"] or ""
        kind = response["x_roi"]["cache"]
        if kind == "miss":
            answer_by_content[content] = expected
        if kind == "semantic":
            counts_dict["semantic_hits"] += 1
            if answer_by_content.get(content) != expected:
                counts_dict["semantic_wrong_hits"] += 1
        return kind

    try:
        for g_index, group in enumerate(query_dict["groups"]):
            run_id = f"e4-cache-{g_index}"

            await ask(group["base"], group["answer"], run_id)
            for _ in range(query_dict["exact_repeats"]):
                counts_dict["exact_repeats"] += 1
                counts_dict["exact_repeat_hits"] += (
                    await ask(group["base"], group["answer"], run_id)
                ) == "exact"
            for paraphrase in group["paraphrases"]:
                counts_dict["paraphrases"] += 1
                counts_dict["paraphrase_semantic_hits"] += (
                    await ask(paraphrase, group["answer"], run_id)
                ) == "semantic"
            for near in group["near_misses"]:
                counts_dict["near_misses"] += 1
                counts_dict["near_miss_semantic_hits"] += (
                    await ask(near["query"], near["answer"], run_id)
                ) == "semantic"
    finally:
        semantic.drop()
    return {
        "counts": counts_dict,
        "exact_hit_rate": rate_summary(
            counts_dict["exact_repeat_hits"], counts_dict["exact_repeats"]
        ),
        "paraphrase_semantic_hit_rate": rate_summary(
            counts_dict["paraphrase_semantic_hits"], counts_dict["paraphrases"]
        ),
        "near_miss_semantic_hit_rate": rate_summary(
            counts_dict["near_miss_semantic_hits"], counts_dict["near_misses"]
        ),
        "semantic_wrong_hit_rate": rate_summary(
            counts_dict["semantic_wrong_hits"], counts_dict["semantic_hits"]
        ),
        "distance_threshold": gateway_dict["semantic_distance_threshold"],
        "embedding_model": gateway_dict["embedding_model"],
    }


# ============= Entry Point =============


async def run_e4(tmp_dir: Path, config_file: Path | None = None) -> tuple[dict[str, Any], Path]:
    """
    Run every E4 part and return metrics and the config path used.

    Parameters
    ----------
    tmp_dir : Path
        Scratch directory for E4-only routing tables.
    config_file : Path or None
        Defaults to configs/eval/e4.yaml.
    """
    config_file = config_file or config_path(E4_CONFIG)
    config_dict = load_yaml(config_file)
    settings = get_settings()
    tmp_dir.mkdir(parents=True, exist_ok=True)
    metrics_dict = {
        "provider_mode": config_dict["provider_mode"],
        "fallback": await run_fallback(config_dict, settings, tmp_dir),
        "budget": await run_budget(config_dict, tmp_dir),
        "cache": await run_cache(config_dict, settings, tmp_dir),
    }
    logger.info(
        "e4 complete",
        extra={"summary": json.dumps(metrics_dict["fallback"]["fallback_success_under_faults"])},
    )
    return metrics_dict, config_file
