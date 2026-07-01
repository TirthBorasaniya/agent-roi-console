"""Workflow CRUD and trigger endpoints."""

# ============= Standard Library =============
import json
import uuid
from datetime import datetime, timezone

# ============= Third-Party =============
from fastapi import APIRouter, HTTPException

# ============= Local =============
from database import get_connection
from models import RunResponse, RunTriggerRequest, WorkflowCreate, WorkflowResponse

# ============= Constants =============
router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ============= Endpoints =============

@router.get("", response_model=list[WorkflowResponse])
def list_workflows() -> list[WorkflowResponse]:
    """Return all workflows ordered by creation date descending."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM workflows ORDER BY created_at DESC"
        ).fetchall()
        return [WorkflowResponse(**dict(row)) for row in rows]
    finally:
        conn.close()


@router.post("", response_model=WorkflowResponse, status_code=201)
def create_workflow(payload: WorkflowCreate) -> WorkflowResponse:
    """Create a new workflow record."""
    workflow_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO workflows (id, name, description, value_category, baseline_minutes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                workflow_id,
                payload.name,
                payload.description,
                payload.value_category,
                payload.baseline_minutes,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        return WorkflowResponse(**dict(row))
    finally:
        conn.close()


@router.get("/{workflow_id}", response_model=WorkflowResponse)
def get_workflow(workflow_id: str) -> WorkflowResponse:
    """Return a single workflow by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Workflow not found")
        return WorkflowResponse(**dict(row))
    finally:
        conn.close()


@router.post("/{workflow_id}/run", response_model=RunResponse)
def trigger_workflow_run(workflow_id: str, payload: RunTriggerRequest = RunTriggerRequest()) -> RunResponse:
    """
    Trigger a workflow run synchronously via the LangGraph orchestrator.

    Creates an initial RunRecord with status RUNNING, executes the graph,
    then returns the completed run record.
    """
    conn = get_connection()
    try:
        wf_row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if not wf_row:
            raise HTTPException(status_code=404, detail="Workflow not found")
        wf_dict = dict(wf_row)
    finally:
        conn.close()

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    user_input = payload.input_payload or wf_dict["description"] or wf_dict["name"]

    # insert a RUNNING record first so partial state is visible
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO runs
                (id, workflow_id, status, trigger, input_payload, started_at)
            VALUES (?, ?, 'RUNNING', 'api', ?, ?)""",
            (run_id, workflow_id, payload.input_payload, started_at),
        )
        conn.commit()
    finally:
        conn.close()

    # execute the graph; orchestrator writes the completed record
    try:
        from agents.orchestrator import run_workflow
        run_workflow(workflow_id=workflow_id, run_id=run_id, user_input=user_input)
    except Exception as exc:
        # mark the run as FAILED so the dashboard reflects the error
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE runs SET status = 'FAILED', completed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), run_id),
            )
            conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {exc}")

    # fetch and return the completed run
    conn = get_connection()
    try:
        run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        run_dict = dict(run_row)
        run_dict["workflow_name"] = wf_dict["name"]
        return RunResponse(**run_dict)
    finally:
        conn.close()
