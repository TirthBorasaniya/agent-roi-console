"""Overview, gateway statistics, guard log, results files, and console metadata."""

# ============= Standard Library =============
from typing import Any

# ============= Third-Party =============
import httpx
from fastapi import APIRouter, HTTPException, Query, Request

# ============= Local =============
from roiconsole import __version__
from roiconsole.api import queries
from roiconsole.common.results import read_results
from roiconsole.common.settings import get_settings

# ============= Constants =============
router = APIRouter(prefix="/api", tags=["insights"])
RESULT_NAMES = (
    "e1_step_success",
    "e2_tasks",
    "e3_roi",
    "e4_gateway",
    "e5_guard",
    "inventory",
    "live_tasks",
    "selfhosted_throughput",
)
GATEWAY_HEALTH_TIMEOUT_S = 2.0


# ============= Endpoints =============


@router.get("/meta")
def meta() -> dict[str, Any]:
    """Connector mode, the demo flag for the banner, and which results exist."""
    settings = get_settings()
    return {
        "version": __version__,
        "connector_mode": settings.resolved_connector_mode,
        "o_demo": settings.o_demo_mode,
        "results": {
            name: (r or {}).get("timestamp_utc")
            for name in RESULT_NAMES
            for r in [read_results(name)]
        },
    }


@router.get("/overview")
def overview(
    window_hours: float = Query(default=queries.DEFAULT_WINDOW_HOURS, gt=0),
) -> list[dict[str, Any]]:
    """Per workflow ROI, cost per success, success rate, budget burn, and guard state."""
    return queries.overview(window_hours)


@router.get("/gateway/stats")
async def gateway_stats(
    window_hours: float = Query(default=queries.DEFAULT_WINDOW_HOURS, gt=0),
) -> dict[str, Any]:
    """Call statistics from llm_calls plus live breaker states from the gateway when reachable."""
    stats = queries.gateway_stats(window_hours)
    health_url = get_settings().gateway_url.rstrip("/").removesuffix("/v1") + "/healthz"
    try:
        async with httpx.AsyncClient(timeout=GATEWAY_HEALTH_TIMEOUT_S) as client:
            response = await client.get(health_url)
        stats["breakers"] = response.json().get("breakers") if response.status_code == 200 else None
    except httpx.HTTPError:
        stats["breakers"] = None
    return stats


@router.get("/guard/log")
def guard_log(limit: int = Query(default=200, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Guard actions newest first."""
    return queries.guard_log(limit)


@router.post("/guard/evaluate")
async def guard_evaluate(request: Request) -> dict[str, Any]:
    """Run one guard evaluation now (the scheduler runs it every interval)."""
    guard = request.app.state.guard
    decision_dict = await guard.evaluate_all()
    return {
        wf: {"action": d.action, "detail": d.detail, "window_runs": d.window_runs}
        for wf, d in decision_dict.items()
    }


@router.get("/results/{name}")
def results(name: str) -> dict[str, Any]:
    """Serve a committed results file to the dashboard."""
    if name not in RESULT_NAMES:
        raise HTTPException(status_code=404, detail="unknown results file")
    payload = read_results(name)
    if payload is None:
        raise HTTPException(
            status_code=404, detail=f"results/{name}.json has not been produced yet"
        )
    return payload
