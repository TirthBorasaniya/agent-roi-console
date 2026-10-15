"""Run history and run detail with step traces."""

# ============= Standard Library =============
from typing import Any

# ============= Third-Party =============
from fastapi import APIRouter, HTTPException, Query

# ============= Local =============
from roiconsole.api import queries

# ============= Constants =============
router = APIRouter(prefix="/api/runs", tags=["runs"])
DEFAULT_PAGE_SIZE = 20


# ============= Endpoints =============


@router.get("")
def list_runs(
    workflow_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Return runs newest first."""
    return queries.list_runs(workflow_id, page, page_size)


@router.get("/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """Return one run with steps, tool calls, and model calls."""
    run = queries.run_detail(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run
