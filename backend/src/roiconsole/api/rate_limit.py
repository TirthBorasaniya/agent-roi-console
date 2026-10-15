"""Per-IP sliding-window rate limiting for the run-trigger endpoint (carried over from v1)."""

# ============= Standard Library =============
import logging
import threading
import time

# ============= Third-Party =============
from fastapi import HTTPException, Request

# ============= Local =============
from roiconsole.common.settings import get_settings

# ============= Constants =============
logger = logging.getLogger(__name__)
# stop the per-IP table growing without bound on a public deployment
MAX_TRACKED_CLIENTS = 4096
UNKNOWN_CLIENT = "unknown"


# ============= Limiter =============


class SlidingWindowRateLimiter:
    """
    Sliding-window limiter keyed by client identifier.

    Parameters
    ----------
    limit : int
        Requests allowed per window.
    window_seconds : float
        Window length.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits_dict: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_id: str) -> tuple[bool, float]:
        """
        Record a request and report whether it is allowed.

        Returns
        -------
        o_allowed : bool
            True within the limit.
        retry_after_seconds : float
            Seconds until the oldest hit leaves the window; 0 when allowed.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            self._prune(cutoff)
            recent_list = [hit for hit in self._hits_dict.get(client_id, []) if hit > cutoff]
            if len(recent_list) >= self.limit:
                self._hits_dict[client_id] = recent_list
                return False, max(self.window_seconds - (now - recent_list[0]), 0.0)
            recent_list.append(now)
            self._hits_dict[client_id] = recent_list
            return True, 0.0

    def _prune(self, cutoff: float) -> None:
        """Drop clients whose hits all expired, with a hard ceiling (caller holds the lock)."""
        expired_list = [
            c for c, hits in self._hits_dict.items() if not any(h > cutoff for h in hits)
        ]
        for client_id in expired_list:
            del self._hits_dict[client_id]
        if len(self._hits_dict) > MAX_TRACKED_CLIENTS:
            logger.warning("rate limiter tracking table full, resetting")
            self._hits_dict.clear()

    def reset(self) -> None:
        """Clear all hits."""
        with self._lock:
            self._hits_dict.clear()


_settings = get_settings()
run_limiter = SlidingWindowRateLimiter(_settings.run_rate_limit, _settings.run_rate_window_seconds)


# ============= Dependency =============


def resolve_client_ip(request: Request) -> str:
    """Return the caller IP, honouring the leftmost X-Forwarded-For entry."""
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or UNKNOWN_CLIENT
    return request.client.host if request.client else UNKNOWN_CLIENT


def enforce_run_rate_limit(request: Request) -> None:
    """FastAPI dependency raising 429 when a client exceeds the run limit."""
    client_ip = resolve_client_ip(request)
    o_allowed, retry_after = run_limiter.check(client_ip)
    if not o_allowed:
        retry_after_str = str(max(int(retry_after + 0.999), 1))
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: at most {run_limiter.limit} runs per "
                f"{int(run_limiter.window_seconds)}s."
            ),
            headers={"Retry-After": retry_after_str},
        )
