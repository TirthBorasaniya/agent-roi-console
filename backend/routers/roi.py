"""ROI calculation helper endpoints."""

# ============= Third-Party =============
from fastapi import APIRouter
from pydantic import BaseModel

# ============= Local =============
from services.roi_calculator import compute_net_roi

# ============= Constants =============
router = APIRouter(prefix="/api/roi", tags=["roi"])


# ============= Request / Response =============

class ROICalculateRequest(BaseModel):
    baseline_minutes: float
    loaded_cost_per_hour: float
    input_tokens: int
    output_tokens: int
    input_price_per_1m: float = 3.0
    output_price_per_1m: float = 15.0


class ROICalculateResponse(BaseModel):
    net_roi_usd: float
    token_cost_usd: float
    human_cost_usd: float


# ============= Endpoints =============

@router.post("/calculate", response_model=ROICalculateResponse)
def calculate_roi(payload: ROICalculateRequest) -> ROICalculateResponse:
    """
    Compute net ROI for a given set of parameters.

    Useful for scenario modelling without an actual run.
    """
    net_roi_usd, token_cost_usd = compute_net_roi(
        baseline_minutes=payload.baseline_minutes,
        loaded_cost_per_hour=payload.loaded_cost_per_hour,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        input_price_per_1m=payload.input_price_per_1m,
        output_price_per_1m=payload.output_price_per_1m,
    )
    human_cost_usd = (payload.baseline_minutes / 60.0) * payload.loaded_cost_per_hour
    return ROICalculateResponse(
        net_roi_usd=round(net_roi_usd, 6),
        token_cost_usd=round(token_cost_usd, 6),
        human_cost_usd=round(human_cost_usd, 4),
    )
