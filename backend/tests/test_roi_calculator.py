"""Unit tests for the ROI calculator service."""

# ============= Standard Library =============
import sys
from pathlib import Path

# ============= Local =============
sys.path.insert(0, str(Path(__file__).parent.parent))
from services.roi_calculator import compute_net_roi


def test_positive_roi():
    """High baseline minutes and low token usage yields positive ROI."""
    net_roi, token_cost = compute_net_roi(
        baseline_minutes=60.0,
        loaded_cost_per_hour=75.0,
        input_tokens=1000,
        output_tokens=500,
        input_price_per_1m=3.0,
        output_price_per_1m=15.0,
    )
    # human cost = (60/60)*75 = $75.00; token cost ≈ $0.011
    assert net_roi > 0, f"expected positive ROI, got {net_roi}"
    assert token_cost > 0, f"expected positive token cost, got {token_cost}"
    assert net_roi < 75.0, "roi cannot exceed human cost saved"


def test_negative_roi_edge_case():
    """Very short baseline with massive token usage yields negative ROI without crashing."""
    net_roi, token_cost = compute_net_roi(
        baseline_minutes=0.1,
        loaded_cost_per_hour=10.0,
        input_tokens=10_000_000,
        output_tokens=5_000_000,
        input_price_per_1m=3.0,
        output_price_per_1m=15.0,
    )
    # human cost ≈ $0.017; token cost ≈ $105
    assert net_roi < 0, f"expected negative ROI, got {net_roi}"
    assert token_cost > 0, f"token cost must still be positive, got {token_cost}"
