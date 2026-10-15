"""Fault-injecting wrapper that makes a provider fail with 500s, 429s, or timeouts on demand."""

# ============= Standard Library =============
import asyncio
import random
from dataclasses import dataclass

# ============= Local =============
from roiconsole.gateway.adapters.base import (
    Adapter,
    ProviderRequest,
    ProviderResponse,
    status_error,
)

# ============= Constants =============
FAULT_KINDS = ("none", "http_500", "http_429", "timeout")


# ============= Config =============


@dataclass
class FaultPlan:
    """
    What to inject.

    Attributes
    ----------
    kind : str
        One of FAULT_KINDS.
    rate : float
        Probability that a call is faulted, in [0, 1].
    hang_s : float
        How long a timeout fault hangs; the gateway's timeout must cut it short.
    seed : int
        Seed for reproducible fault sequences.
    provider_models : tuple of str or None
        Only requests for these provider models are faulted; None faults every
        model behind the adapter (one provider can serve several models).
    """

    kind: str = "none"
    rate: float = 0.0
    hang_s: float = 3600.0
    seed: int = 0
    provider_models: tuple[str, ...] | None = None


# ============= Adapter =============


class FaultInjectingAdapter:
    """
    Wrap an adapter and fail a fraction of calls.

    Parameters
    ----------
    inner : Adapter
        Adapter that serves non-faulted calls.
    plan : FaultPlan
        Fault kind and rate; can be replaced at runtime through `plan`.
    """

    def __init__(self, inner: Adapter, plan: FaultPlan | None = None) -> None:
        self.provider = inner.provider
        self._inner = inner
        self.plan = plan or FaultPlan()
        self._rng = random.Random(self.plan.seed)
        self.injected = 0

    def set_plan(self, plan: FaultPlan) -> None:
        """Replace the fault plan and reseed."""
        if plan.kind not in FAULT_KINDS:
            raise ValueError(f"fault kind must be one of {FAULT_KINDS}")
        self.plan = plan
        self._rng = random.Random(plan.seed)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        """Fail according to the plan, else delegate."""
        o_targeted = (
            self.plan.provider_models is None or request.provider_model in self.plan.provider_models
        )
        if self.plan.kind != "none" and o_targeted and self._rng.random() < self.plan.rate:
            self.injected += 1
            if self.plan.kind == "timeout":
                await asyncio.sleep(self.plan.hang_s)
            status = 429 if self.plan.kind == "http_429" else 500
            raise status_error(status, f"injected {self.plan.kind}")
        return await self._inner.complete(request)
