"""Unit tests for the per-IP sliding-window rate limiter."""

# ============= Standard Library =============
import sys
from pathlib import Path

# ============= Local =============
sys.path.insert(0, str(Path(__file__).parent.parent))
from services.rate_limiter import SlidingWindowRateLimiter


def test_allows_up_to_limit_then_blocks():
    """Assert requests are allowed up to the limit and refused past it."""
    limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)

    for _ in range(3):
        o_allowed, retry_after = limiter.check("1.2.3.4")
        assert o_allowed is True
        assert retry_after == 0.0

    o_allowed, retry_after = limiter.check("1.2.3.4")
    assert o_allowed is False
    assert 0.0 < retry_after <= 60.0


def test_limit_is_tracked_per_client():
    """Assert one client exhausting its quota does not block another."""
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)

    assert limiter.check("10.0.0.1")[0] is True
    assert limiter.check("10.0.0.1")[0] is False

    # a different IP still has its own budget
    assert limiter.check("10.0.0.2")[0] is True


def test_window_expiry_restores_budget():
    """Assert hits older than the window no longer count against the limit."""
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=0.05)

    assert limiter.check("5.5.5.5")[0] is True
    assert limiter.check("5.5.5.5")[0] is False

    # a zero-length wait would be flaky, so drive expiry through the window
    import time

    time.sleep(0.06)
    assert limiter.check("5.5.5.5")[0] is True


def test_reset_clears_state():
    """Assert reset restores a fully unused limiter."""
    limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)

    assert limiter.check("9.9.9.9")[0] is True
    assert limiter.check("9.9.9.9")[0] is False

    limiter.reset()
    assert limiter.check("9.9.9.9")[0] is True
