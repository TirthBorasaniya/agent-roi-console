"""Workflow endpoints: list, detail, run, resume, ROI, and sensitivity."""

# ============= Standard Library =============
from typing import Annotated, Any

# ============= Third-Party =============
from fastapi import APIRouter, Depends, HTTPException, Query

# ============= Local =============
from roiconsole.api import queries
from roiconsole.api.deps import RunnerHolder, RunRequest, get_holder
from roiconsole.api.rate_limit import enforce_run_rate_limit
from roiconsole.guard.service import load_state, save_state
from roiconsole.guard.state_machine import PHASE_NORMAL
from roiconsole.paths.registry import PATHS_DICT
from roiconsole.workflows.registry import load_tasks, load_workflows, tasks_for

# ============= Constants =============
router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ============= Helpers =============


def require_workflow(workflow_id: str) -> None:
    """Raise 404 for an unknown workflow."""
    if workflow_id not in load_workflows():
        raise HTTPException(status_code=404, detail="Workflow not found")


# ============= Endpoints =============


@router.get("")
def list_workflows() -> list[dict[str, Any]]:
    """Return every workflow with its assumptions and guard state."""
    return [queries.workflow_view(w) for w in load_workflows()]


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Return one workflow with its model mix."""
    require_workflow(workflow_id)
    return {**queries.workflow_view(workflow_id), "model_mix": queries.model_mix(workflow_id)}


@router.post("/{workflow_id}/run", dependencies=[Depends(enforce_run_rate_limit)])
async def run_workflow(
    workflow_id: str,
    holder: Annotated[RunnerHolder, Depends(get_holder)],
    body: RunRequest | None = None,
) -> dict[str, Any]:
    """
    Run one task of a workflow on its active path (or the requested path).

    Rate limited per client IP, refused with 409 while the guard has the workflow
    paused. Defaults to the first development task.
    """
    require_workflow(workflow_id)
    body = body or RunRequest()
    workflow = load_workflows()[workflow_id]
    state = load_state(workflow_id, workflow.default_path)
    if state.phase == "paused":
        raise HTTPException(status_code=409, detail="workflow is paused by the ROI guard")
    path = body.path or state.active_path
    if path not in PATHS_DICT:
        raise HTTPException(status_code=400, detail=f"unknown path {path}")
    task = load_tasks().get(body.task_id) if body.task_id else tasks_for("dev", workflow_id)[0]
    if task is None or task.workflow_id != workflow_id:
        raise HTTPException(status_code=400, detail="task does not belong to this workflow")
    async with holder.lock:
        outcome = await holder.runner.run(task, path, body.model, trigger="api", o_cache=True)
    return outcome.to_dict()


@router.post("/{workflow_id}/resume")
def resume_workflow(workflow_id: str) -> dict[str, Any]:
    """Clear a guard pause; the workflow keeps its downgraded path."""
    require_workflow(workflow_id)
    workflow = load_workflows()[workflow_id]
    state = load_state(workflow_id, workflow.default_path)
    save_state(
        state.__class__(workflow_id, state.active_path, PHASE_NORMAL, state.phase_run_offset)
    )
    return queries.workflow_view(workflow_id)


@router.get("/{workflow_id}/roi")
def get_roi(
    workflow_id: str, window_hours: float = Query(default=queries.DEFAULT_WINDOW_HOURS, gt=0)
) -> dict[str, Any]:
    """ROI over the window, overall and per path."""
    require_workflow(workflow_id)
    return queries.roi_view(workflow_id, window_hours)


@router.get("/{workflow_id}/sensitivity")
def get_sensitivity(
    workflow_id: str, window_hours: float = Query(default=queries.DEFAULT_WINDOW_HOURS, gt=0)
) -> dict[str, Any]:
    """Sensitivity grid with break-even minutes per task."""
    require_workflow(workflow_id)
    return queries.sensitivity_view(workflow_id, window_hours)
