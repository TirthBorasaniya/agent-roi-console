"""Run history and status endpoints."""

# ============= Standard Library =============
from typing import Optional

# ============= Third-Party =============
from fastapi import APIRouter, HTTPException, Query

# ============= Local =============
from database import get_connection
from models import RunResponse, ToolUsageRecord

# ============= Constants =============
router = APIRouter(prefix="/api/runs", tags=["runs"])
DEFAULT_PAGE_SIZE = 20


# ============= Endpoints =============

@router.get("", response_model=list[RunResponse])
def list_runs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=100),
) -> list[RunResponse]:
    """Return paginated run history with workflow name joined."""
    offset = (page - 1) * page_size
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT r.*, w.name as workflow_name
               FROM runs r
               LEFT JOIN workflows w ON r.workflow_id = w.id
               ORDER BY r.started_at DESC
               LIMIT ? OFFSET ?""",
            (page_size, offset),
        ).fetchall()
        return [RunResponse(**dict(row)) for row in rows]
    finally:
        conn.close()


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> RunResponse:
    """Return a single run by ID including per-tool usage records."""
    conn = get_connection()
    try:
        run_row = conn.execute(
            """SELECT r.*, w.name as workflow_name
               FROM runs r
               LEFT JOIN workflows w ON r.workflow_id = w.id
               WHERE r.id = ?""",
            (run_id,),
        ).fetchone()
        if not run_row:
            raise HTTPException(status_code=404, detail="Run not found")
        run_dict = dict(run_row)

        tool_rows = conn.execute(
            "SELECT * FROM tool_usage WHERE run_id = ? ORDER BY called_at",
            (run_id,),
        ).fetchall()
        tool_usage_list = [ToolUsageRecord(**dict(tr)) for tr in tool_rows]

        run_dict["tool_usage"] = tool_usage_list
        return RunResponse(**run_dict)
    finally:
        conn.close()
