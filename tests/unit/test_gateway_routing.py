"""Unit tests for routing, budgets, breaker transitions, cost estimates, and pricing coverage."""

# ============= Third-Party =============
import pytest

# ============= Local =============
from roiconsole.common.config import config_path
from roiconsole.gateway.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from roiconsole.gateway.budgets import BudgetConfig, BudgetTracker, WorkflowBudget
from roiconsole.gateway.pricing import PriceTable
from roiconsole.gateway.router import RoutingTable, plan_route
from roiconsole.gateway.service import missing_price_models
from roiconsole.gateway.store import MemoryStore
from tests.gateway_helpers import CHEAP, MID, PRICEY, price_table, routing_table

pytestmark = pytest.mark.unit


# ============= Routing =============


def test_cheapest_candidate_meeting_bar_goes_first(tmp_path):
    table = routing_table(tmp_path, {CHEAP: 0.85, MID: 0.92, PRICEY: 0.99})
    plan = plan_route(table, "extraction", 0.9)
    assert plan.models == [MID, PRICEY] and not plan.o_below_bar


def test_all_meeting_bar_orders_by_cost(tmp_path):
    plan = plan_route(routing_table(tmp_path), "extraction", 0.9)
    assert plan.models == [CHEAP, MID, PRICEY]


def test_no_candidate_meets_bar_orders_by_success_and_flags(tmp_path):
    table = routing_table(tmp_path, {CHEAP: 0.5, MID: 0.8, PRICEY: 0.7})
    plan = plan_route(table, "extraction", 0.9)
    assert plan.models == [MID, PRICEY, CHEAP] and plan.o_below_bar


def test_unknown_step_class_raises(tmp_path):
    with pytest.raises(KeyError):
        plan_route(routing_table(tmp_path), "planning", 0.9)


def test_routing_table_round_trips(tmp_path):
    table = routing_table(tmp_path)
    out = tmp_path / "copy.yaml"
    table.write(out)
    assert RoutingTable.load(out).to_dict() == table.to_dict()


# ============= Pricing Coverage =============


def test_every_routing_table_model_has_a_price(tmp_path):
    assert missing_price_models(routing_table(tmp_path), price_table()) == []
    committed = config_path("routing_table.yaml")
    if committed.is_file():
        assert missing_price_models(RoutingTable.load(committed), PriceTable.load()) == []


def test_missing_price_is_reported(tmp_path):
    table = routing_table(tmp_path)
    prices = PriceTable({}, {})
    assert set(missing_price_models(table, prices)) == {CHEAP, MID, PRICEY}


def test_cost_estimate_uses_max_tokens_and_actual_uses_usage():
    prices = price_table()
    assert prices.estimate(MID, 1000, 500) == pytest.approx((1000 * 1.0 + 500 * 4.0) / 1e6)
    assert prices.actual(MID, 1000, 100) == pytest.approx((1000 * 1.0 + 100 * 4.0) / 1e6)


# ============= Budgets =============


async def test_budget_remaining_is_min_of_run_and_day():
    store = MemoryStore()
    tracker = BudgetTracker(BudgetConfig(WorkflowBudget(1.0, 1.5), {}), store)
    await tracker.charge("wf", "run1", 0.6)
    await tracker.charge("wf", "run2", 0.6)
    assert await tracker.remaining("wf", "run1") == pytest.approx(0.3)
    assert await tracker.remaining("wf", "run3") == pytest.approx(0.3)


def test_budget_config_loads_overrides():
    config = BudgetConfig.load(config_path("budgets.yaml"))
    assert config.for_workflow("crm_pipeline").per_run_usd > 0
    assert config.for_workflow("unknown").per_run_usd == config.defaults.per_run_usd


# ============= Circuit Breaker =============


class FakeClock:
    """Manually advanced clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_five_failures_in_window():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state == CLOSED
    breaker.record_failure()
    assert breaker.state == OPEN and not breaker.allow()


def test_breaker_failures_outside_window_do_not_count():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(4):
        breaker.record_failure()
    clock.now = 61.0
    breaker.record_failure()
    assert breaker.state == CLOSED


def test_breaker_half_opens_after_cooldown_and_allows_one_trial():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.now = 29.9
    assert breaker.state == OPEN
    clock.now = 30.0
    assert breaker.state == HALF_OPEN
    assert breaker.allow() and not breaker.allow()


def test_breaker_trial_success_closes_and_failure_reopens():
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(5):
        breaker.record_failure()
    clock.now = 30.0
    assert breaker.allow()
    breaker.record_success()
    assert breaker.state == CLOSED
    for _ in range(5):
        breaker.record_failure()
    clock.now = 60.0
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state == OPEN
