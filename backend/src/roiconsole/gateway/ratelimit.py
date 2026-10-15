"""Per-workflow token-bucket rate limiting on requests and tokens per minute."""

# ============= Standard Library =============
import asyncio
import time
from dataclasses import dataclass

# ============= Local =============
from roiconsole.gateway.store import GatewayStore

# ============= Constants =============
POLL_S = 0.25
SECONDS_PER_MINUTE = 60.0


# ============= Limiter =============


@dataclass(frozen=True)
class RateLimit:
    """Requests and tokens allowed per minute."""

    requests_per_minute: float
    tokens_per_minute: float


class RateLimitExceededError(RuntimeError):
    """Raised when a request cannot get tokens within the wait budget."""


class WorkflowRateLimiter:
    """
    Two token buckets per workflow, one for requests and one for tokens.

    A request waits up to `max_wait_s` for both buckets, so bursts are smoothed
    to stay inside provider limits instead of producing provider 429s.

    Parameters
    ----------
    store : GatewayStore
        Bucket storage.
    limits_dict : dict
        workflow_id to RateLimit; key "default" applies to the rest.
    max_wait_s : float
        Longest a request waits before failing.
    """

    def __init__(
        self, store: GatewayStore, limits_dict: dict[str, RateLimit], max_wait_s: float
    ) -> None:
        self._store = store
        self._limits_dict = limits_dict
        self._max_wait_s = max_wait_s

    def limit_for(self, workflow_id: str) -> RateLimit:
        """Return a workflow's limits."""
        return self._limits_dict.get(workflow_id, self._limits_dict["default"])

    async def acquire(self, workflow_id: str, estimated_tokens: int) -> float:
        """
        Take one request token and `estimated_tokens` token-bucket tokens.

        Returns
        -------
        waited_s : float
            Time spent waiting.

        Raises
        ------
        RateLimitExceededError
            When the buckets do not refill within max_wait_s.
        """
        limit = self.limit_for(workflow_id)
        token_cost = min(float(estimated_tokens), limit.tokens_per_minute)
        started = time.monotonic()
        o_request_taken = False
        while True:
            if not o_request_taken:
                o_request_taken, _ = await self._store.take_tokens(
                    f"rl:{workflow_id}:requests",
                    limit.requests_per_minute,
                    limit.requests_per_minute / SECONDS_PER_MINUTE,
                    1.0,
                )
            if o_request_taken:
                o_tokens_taken, _ = await self._store.take_tokens(
                    f"rl:{workflow_id}:tokens",
                    limit.tokens_per_minute,
                    limit.tokens_per_minute / SECONDS_PER_MINUTE,
                    token_cost,
                )
                if o_tokens_taken:
                    return time.monotonic() - started
            if time.monotonic() - started >= self._max_wait_s:
                raise RateLimitExceededError(
                    f"rate limit for {workflow_id} not available within {self._max_wait_s}s"
                )
            await asyncio.sleep(POLL_S)
