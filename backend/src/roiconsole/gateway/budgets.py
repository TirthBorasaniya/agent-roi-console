"""Per-run and per-workflow-per-day budget accounting for the gateway."""

# ============= Standard Library =============
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# ============= Local =============
from roiconsole.common.config import config_path, load_yaml
from roiconsole.gateway.store import GatewayStore

# ============= Constants =============
BUDGETS_FILE = "budgets.yaml"
RUN_KEY_TTL_S = 7 * 24 * 3600
DAY_KEY_TTL_S = 2 * 24 * 3600


# ============= Config =============


@dataclass(frozen=True)
class WorkflowBudget:
    """Limits for one workflow."""

    per_run_usd: float
    per_workflow_daily_usd: float


class BudgetConfig:
    """
    Budgets from configs/budgets.yaml, with per-workflow overrides of the defaults.

    Parameters
    ----------
    defaults : WorkflowBudget
        Limits for workflows without overrides.
    overrides_dict : dict
        workflow_id to WorkflowBudget.
    """

    def __init__(self, defaults: WorkflowBudget, overrides_dict: dict[str, WorkflowBudget]) -> None:
        self.defaults = defaults
        self._overrides_dict = overrides_dict

    @classmethod
    def load(cls, path: Path | None = None) -> "BudgetConfig":
        """Load the YAML file."""
        data = load_yaml(path or config_path(BUDGETS_FILE))
        defaults_dict = data["defaults"]
        defaults = WorkflowBudget(
            float(defaults_dict["per_run_usd"]), float(defaults_dict["per_workflow_daily_usd"])
        )
        overrides_dict = {
            workflow_id: WorkflowBudget(
                float((entry or {}).get("per_run_usd", defaults.per_run_usd)),
                float((entry or {}).get("per_workflow_daily_usd", defaults.per_workflow_daily_usd)),
            )
            for workflow_id, entry in (data.get("workflows") or {}).items()
        }
        return cls(defaults, overrides_dict)

    def for_workflow(self, workflow_id: str) -> WorkflowBudget:
        """Return the limits for a workflow."""
        return self._overrides_dict.get(workflow_id, self.defaults)


# ============= Tracker =============


def _day(now: datetime | None = None) -> str:
    """Return the UTC date used for daily budgets."""
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


class BudgetTracker:
    """
    Track spend per run and per workflow per UTC day.

    Parameters
    ----------
    config : BudgetConfig
        Limits.
    store : GatewayStore
        Counter storage shared by gateway replicas.
    """

    def __init__(self, config: BudgetConfig, store: GatewayStore) -> None:
        self.config = config
        self._store = store

    async def spent(self, workflow_id: str, run_id: str) -> tuple[float, float]:
        """Return (run spend, workflow spend today) in USD."""
        run_spent = await self._store.get_float(f"budget:run:{run_id}")
        day_spent = await self._store.get_float(f"budget:wf:{workflow_id}:{_day()}")
        return run_spent, day_spent

    async def remaining(self, workflow_id: str, run_id: str) -> float:
        """Return the smaller of the run's and the workflow-day's remaining budget."""
        limits = self.config.for_workflow(workflow_id)
        run_spent, day_spent = await self.spent(workflow_id, run_id)
        return min(limits.per_run_usd - run_spent, limits.per_workflow_daily_usd - day_spent)

    async def charge(self, workflow_id: str, run_id: str, cost_usd: float) -> None:
        """Record actual spend for a completed call."""
        if cost_usd <= 0:
            return
        await self._store.incr_float(f"budget:run:{run_id}", cost_usd, RUN_KEY_TTL_S)
        await self._store.incr_float(f"budget:wf:{workflow_id}:{_day()}", cost_usd, DAY_KEY_TTL_S)
