"""Statistics helpers: Wilson score intervals, percentiles, and simple summaries."""

# ============= Standard Library =============
import math
from collections import Counter

# ============= Constants =============
Z_95 = 1.959963984540054


# ============= Intervals =============


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """
    Wilson score interval for a binomial proportion.

    Parameters
    ----------
    successes : int
        Number of successes.
    trials : int
        Number of trials.
    z : float
        Normal quantile; 1.96 gives a 95 percent interval.

    Returns
    -------
    low, high : float
        Interval bounds in [0, 1]; (0, 1) when there are no trials.
    """
    if trials <= 0:
        return 0.0, 1.0
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between 0 and trials")
    p_hat = successes / trials
    denominator = 1 + z**2 / trials
    center = (p_hat + z**2 / (2 * trials)) / denominator
    margin = z * math.sqrt(p_hat * (1 - p_hat) / trials + z**2 / (4 * trials**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def rate_summary(successes: int, trials: int) -> dict[str, float | int | None]:
    """Return {successes, trials, rate, ci95_low, ci95_high}."""
    low, high = wilson_interval(successes, trials)
    return {
        "successes": successes,
        "trials": trials,
        "rate": successes / trials if trials else None,
        "ci95_low": low,
        "ci95_high": high,
    }


# ============= Distributions =============


def percentile(value_list: list[float], q: float) -> float | None:
    """Linear-interpolation percentile, q in [0, 1]; None for an empty list."""
    if not value_list:
        return None
    ordered = sorted(value_list)
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def mean(value_list: list[float]) -> float | None:
    """Arithmetic mean, None for an empty list."""
    return sum(value_list) / len(value_list) if value_list else None


def distribution(value_list: list[int | str]) -> dict[str, int]:
    """Count occurrences, keyed by the string form of each value."""
    return {str(k): v for k, v in sorted(Counter(value_list).items(), key=lambda kv: str(kv[0]))}
