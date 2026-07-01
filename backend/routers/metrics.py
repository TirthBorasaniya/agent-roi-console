"""Usage metrics and cost attribution endpoints."""

# ============= Third-Party =============
from fastapi import APIRouter

# ============= Local =============
from database import get_connection
from models import CostByTool, MetricsSummary, ROIByCategory, TimelinePoint

# ============= Constants =============
router = APIRouter(prefix="/api/metrics", tags=["metrics"])


# ============= Endpoints =============

@router.get("/summary", response_model=MetricsSummary)
def get_summary() -> MetricsSummary:
    """Return aggregated totals: net ROI, run count, avg token cost, active workflows."""
    conn = get_connection()
    try:
        runs_row = conn.execute(
            """SELECT
                COALESCE(SUM(net_roi_usd), 0.0) as total_net_roi_usd,
                COUNT(*) as total_runs,
                COALESCE(AVG(token_cost_usd), 0.0) as avg_token_cost_usd
            FROM runs
            WHERE status = 'COMPLETED'"""
        ).fetchone()
        wf_row = conn.execute(
            "SELECT COUNT(*) as active_workflows FROM workflows"
        ).fetchone()
        return MetricsSummary(
            total_net_roi_usd=round(runs_row["total_net_roi_usd"], 4),
            total_runs=runs_row["total_runs"],
            avg_token_cost_usd=round(runs_row["avg_token_cost_usd"], 6),
            active_workflows=wf_row["active_workflows"],
        )
    finally:
        conn.close()


@router.get("/roi-by-category", response_model=list[ROIByCategory])
def get_roi_by_category() -> list[ROIByCategory]:
    """Return total net ROI aggregated by workflow value_category."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT
                w.value_category,
                COALESCE(SUM(r.net_roi_usd), 0.0) as total_net_roi_usd,
                COUNT(r.id) as run_count
            FROM workflows w
            LEFT JOIN runs r ON w.id = r.workflow_id AND r.status = 'COMPLETED'
            GROUP BY w.value_category
            ORDER BY total_net_roi_usd DESC"""
        ).fetchall()
        return [
            ROIByCategory(
                value_category=row["value_category"],
                total_net_roi_usd=round(row["total_net_roi_usd"], 4),
                run_count=row["run_count"],
            )
            for row in rows
        ]
    finally:
        conn.close()


@router.get("/cost-by-tool", response_model=list[CostByTool])
def get_cost_by_tool() -> list[CostByTool]:
    """Return token cost aggregated by tool_name across all runs."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT
                tool_name,
                COALESCE(SUM(cost_usd), 0.0) as total_cost_usd,
                COUNT(*) as call_count,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens
            FROM tool_usage
            GROUP BY tool_name
            ORDER BY total_cost_usd DESC"""
        ).fetchall()
        return [
            CostByTool(
                tool_name=row["tool_name"],
                total_cost_usd=round(row["total_cost_usd"], 6),
                call_count=row["call_count"],
                total_input_tokens=row["total_input_tokens"],
                total_output_tokens=row["total_output_tokens"],
            )
            for row in rows
        ]
    finally:
        conn.close()


@router.get("/timeline", response_model=list[TimelinePoint])
def get_timeline() -> list[TimelinePoint]:
    """Return daily run counts and total net ROI for the last 30 days."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT
                DATE(started_at) as date,
                COUNT(*) as run_count,
                COALESCE(SUM(net_roi_usd), 0.0) as total_net_roi_usd
            FROM runs
            WHERE status = 'COMPLETED'
                AND started_at >= DATE('now', '-30 days')
            GROUP BY DATE(started_at)
            ORDER BY date ASC"""
        ).fetchall()
        return [
            TimelinePoint(
                date=row["date"],
                run_count=row["run_count"],
                total_net_roi_usd=round(row["total_net_roi_usd"], 4),
            )
            for row in rows
        ]
    finally:
        conn.close()
