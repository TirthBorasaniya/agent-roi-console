"""In-memory per-IP sliding-window rate limiting for run-triggering endpoints."""

# ============= Standard Library =============
import logging
import os
import threading
import time

# ============= Third-Party =============
from fastapi import HTTPException, Request

# ============= Constants =============
logger = logging.getLogger(__name__)

RUN_RATE_LIMIT = int(os.getenv("RUN_RATE_LIMIT", "10"))
RUN_RATE_WINDOW_SECONDS = float(os.getenv("RUN_RATE_WINDOW_SECONDS", "60"))

# stop the per-IP table growing without bound on a public deployment
MAX_TRACKED_CLIENTS = 4096
UNKNOWN_CLIENT = "unknown"


# ============= Limiter =============

class SlidingWindowRateLimiter:
    """
    Fixed-capacity sliding-window limiter keyed by client identifier.

    State is held on the instance rather than at module scope so it stays
    encapsulated and can be reset in tests.

    Parameters
    ----------
    limit : int
        Maximum number of permitted requests per window.
    window_seconds : float
        Length of the sliding window in seconds.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._hits_dict: dict[str, list[float]] = {}
        # sync endpoints run in a threadpool, so this state is touched concurrently
        self._lock = threading.Lock()

    def check(self, client_id: str) -> tuple[bool, float]:
        """
        Record a request for a client and report whether it is allowed.

        Parameters
        ----------
        client_id : str
            Identifier for the caller, normally its IP address.

        Returns
        -------
        o_allowed : bool
            True when the request is within the limit.
        retry_after_seconds : float
            Seconds until the oldest hit leaves the window; 0.0 when allowed.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds

        with self._lock:
            self._prune(cutoff)

            recent_list = [hit for hit in self._hits_dict.get(client_id, []) if hit > cutoff]

            if len(recent_list) >= self._limit:
                self._hits_dict[client_id] = recent_list
                retry_after = self._window_seconds - (now - recent_list[0])
                return False, max(retry_after, 0.0)

            recent_list.append(now)
            self._hits_dict[client_id] = recent_list
            return True, 0.0

    def _prune(self, cutoff: float) -> None:
        """
        Drop clients whose hits have all aged out, bounding memory use.

        Parameters
        ----------
        cutoff : float
            Monotonic timestamp before which hits are considered expired.

        Returns
        -------
        None
        """
        expired_list = [
            client_id
            for client_id, hits_list in self._hits_dict.items()
            if not any(hit > cutoff for hit in hits_list)
        ]
        for client_id in expired_list:
            del self._hits_dict[client_id]

        # hard ceiling in case a flood of distinct IPs arrives inside one window
        if len(self._hits_dict) > MAX_TRACKED_CLIENTS:
            logger.warning("rate limiter tracking table full, resetting")
            self._hits_dict.clear()

    def reset(self) -> None:
        """
        Clear all recorded hits.

        Returns
        -------
        None
        """
        with self._lock:
            self._hits_dict.clear()


# single shared limiter for the run endpoint
run_limiter = SlidingWindowRateLimiter(RUN_RATE_LIMIT, RUN_RATE_WINDOW_SECONDS)


# ============= Helpers =============

def resolve_client_ip(request: Request) -> str:
    """
    Determine the caller's IP, honouring the proxy header used by PaaS platforms.

    Parameters
    ----------
    request : Request
        Incoming request.

    Returns
    -------
    str
        Client IP address, or "unknown" when it cannot be determined.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # leftmost entry is the original client; the rest are proxy hops
        return forwarded_for.split(",")[0].strip() or UNKNOWN_CLIENT
    if request.client is not None:
        return request.client.host
    return UNKNOWN_CLIENT


# ============= Dependency =============

def enforce_run_rate_limit(request: Request) -> None:
    """
    FastAPI dependency that rate limits run triggering per client IP.

    Parameters
    ----------
    request : Request
        Incoming request, used to resolve the client IP.

    Raises
    ------
    HTTPException
        429 when the caller has exceeded the configured limit.

    Returns
    -------
    None
    """
    client_ip = resolve_client_ip(request)
    o_allowed, retry_after_seconds = run_limiter.check(client_ip)

    if not o_allowed:
        retry_after_str = str(max(int(retry_after_seconds + 0.999), 1))
        logger.warning(f"rate limit hit by {client_ip}")
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: at most {RUN_RATE_LIMIT} runs per "
                f"{int(RUN_RATE_WINDOW_SECONDS)}s. Retry in {retry_after_str}s."
            ),
            headers={"Retry-After": retry_after_str},
        )
