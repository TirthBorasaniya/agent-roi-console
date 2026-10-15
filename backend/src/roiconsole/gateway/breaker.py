"""Per-model circuit breaker: opens after repeated failures, half-opens after a cool-down."""

# ============= Standard Library =============
import threading
import time
from collections import deque
from collections.abc import Callable

# ============= Constants =============
CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"
STATE_GAUGE_DICT = {CLOSED: 0.0, HALF_OPEN: 0.5, OPEN: 1.0}


# ============= Breaker =============


class CircuitBreaker:
    """
    Circuit breaker for one provider model.

    Closed: calls pass; failures inside the window are counted. When
    `failure_threshold` failures fall within `window_s`, the breaker opens.
    Open: calls are refused until `half_open_after_s` has elapsed, then the
    breaker half-opens and lets one trial call through. A trial success closes
    it; a trial failure reopens it.

    Parameters
    ----------
    failure_threshold : int
        Failures within the window that open the circuit (default 5).
    window_s : float
        Sliding window for counting failures (default 60).
    half_open_after_s : float
        Cool-down before a trial call (default 30).
    clock : callable
        Monotonic clock, injectable for tests.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        window_s: float = 60.0,
        half_open_after_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._window_s = window_s
        self._half_open_after_s = half_open_after_s
        self._clock = clock
        self._failures: deque[float] = deque()
        self._state = CLOSED
        self._opened_at = 0.0
        self._trial_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Return the current state, moving open to half-open once the cool-down passes."""
        with self._lock:
            self._refresh()
            return self._state

    def _refresh(self) -> None:
        """Advance open to half-open when due (caller holds the lock)."""
        if self._state == OPEN and self._clock() - self._opened_at >= self._half_open_after_s:
            self._state = HALF_OPEN
            self._trial_in_flight = False

    def allow(self) -> bool:
        """Return True when a call may proceed; in half-open, only one trial at a time."""
        with self._lock:
            self._refresh()
            if self._state == CLOSED:
                return True
            if self._state == HALF_OPEN and not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            return False

    def record_success(self) -> None:
        """Close the circuit after a success."""
        with self._lock:
            self._state = CLOSED
            self._failures.clear()
            self._trial_in_flight = False

    def record_failure(self) -> None:
        """Count a failure and open the circuit when the threshold is reached."""
        with self._lock:
            now = self._clock()
            if self._state == HALF_OPEN:
                self._open(now)
                return
            self._failures.append(now)
            while self._failures and now - self._failures[0] > self._window_s:
                self._failures.popleft()
            if len(self._failures) >= self._threshold:
                self._open(now)

    def _open(self, now: float) -> None:
        """Open the circuit (caller holds the lock)."""
        self._state = OPEN
        self._opened_at = now
        self._failures.clear()
        self._trial_in_flight = False


class BreakerRegistry:
    """
    One breaker per model, created on first use.

    Parameters
    ----------
    factory : callable
        Returns a new CircuitBreaker.
    """

    def __init__(self, factory: Callable[[], CircuitBreaker] = CircuitBreaker) -> None:
        self._factory = factory
        self._breakers_dict: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, model: str) -> CircuitBreaker:
        """Return the breaker for a model."""
        with self._lock:
            if model not in self._breakers_dict:
                self._breakers_dict[model] = self._factory()
            return self._breakers_dict[model]

    def states(self) -> dict[str, str]:
        """Return model to state for every breaker created so far."""
        with self._lock:
            model_list = list(self._breakers_dict)
        return {model: self.get(model).state for model in model_list}
