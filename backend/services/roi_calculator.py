"""ROI and cost attribution calculation logic for completed workflow runs."""


# ============= Section: Core Calculation =============

def compute_net_roi(
    baseline_minutes: float,
    loaded_cost_per_hour: float,
    input_tokens: int,
    output_tokens: int,
    input_price_per_1m: float,
    output_price_per_1m: float,
) -> tuple[float, float]:
    """
    Compute net ROI and token cost for a single workflow run.

    Parameters
    ----------
    baseline_minutes : float
        Estimated human time this workflow replaces, in minutes.
    loaded_cost_per_hour : float
        Fully loaded hourly labor cost in USD.
    input_tokens : int
        Total input tokens consumed across all LLM calls in the run.
    output_tokens : int
        Total output tokens generated across all LLM calls in the run.
    input_price_per_1m : float
        Price per 1M input tokens in USD.
    output_price_per_1m : float
        Price per 1M output tokens in USD.

    Returns
    -------
    net_roi_usd : float
        Net ROI in USD: human cost saved minus token cost.
    token_cost_usd : float
        Total token cost for this run in USD.
    """
    human_cost = (baseline_minutes / 60.0) * loaded_cost_per_hour
    token_cost = (
        (input_tokens * input_price_per_1m / 1_000_000)
        + (output_tokens * output_price_per_1m / 1_000_000)
    )
    return human_cost - token_cost, token_cost
