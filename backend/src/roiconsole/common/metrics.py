"""Prometheus metric definitions shared by the gateway and the console API."""

# ============= Third-Party =============
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# ============= Constants =============
REGISTRY = CollectorRegistry(auto_describe=True)
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

# ============= Gateway Metrics =============
LLM_COST_USD_TOTAL = Counter(
    "roiconsole_llm_cost_usd_total",
    "Accumulated model cost in USD.",
    ["workflow", "model"],
    registry=REGISTRY,
)
LLM_CALLS_TOTAL = Counter(
    "roiconsole_llm_calls_total",
    "Model calls by outcome.",
    ["model", "outcome"],
    registry=REGISTRY,
)
CACHE_HITS_TOTAL = Counter(
    "roiconsole_cache_hits_total",
    "Gateway cache hits by kind.",
    ["kind"],
    registry=REGISTRY,
)
BUDGET_HALTS_TOTAL = Counter(
    "roiconsole_budget_halts_total",
    "Requests halted because the budget was exhausted.",
    ["workflow"],
    registry=REGISTRY,
)
LLM_LATENCY_SECONDS = Histogram(
    "roiconsole_llm_latency_seconds",
    "Provider call latency in seconds.",
    ["model"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
CIRCUIT_STATE = Gauge(
    "roiconsole_circuit_open",
    "1 when the provider circuit for a model is open, 0.5 half-open, 0 closed.",
    ["model"],
    registry=REGISTRY,
)

# ============= Console Metrics =============
RUNS_TOTAL = Counter(
    "roiconsole_runs_total",
    "Workflow runs by workflow, path, and outcome.",
    ["workflow", "path", "outcome"],
    registry=REGISTRY,
)
GUARD_ACTIONS_TOTAL = Counter(
    "roiconsole_guard_actions_total",
    "ROI guard actions by workflow and action.",
    ["workflow", "action"],
    registry=REGISTRY,
)
