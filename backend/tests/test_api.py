"""Integration tests for the FastAPI backend."""

# ============= Standard Library =============
import sys
from pathlib import Path

# ============= Third-Party =============
import pytest
from fastapi.testclient import TestClient

# ============= Local =============
sys.path.insert(0, str(Path(__file__).parent.parent))
from main import app

# ============= Fixtures =============

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ============= Tests =============

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_list_workflow(client):
    """POST /api/workflows creates a record; GET returns it in the list."""
    payload = {
        "name": "Test Workflow",
        "description": "A test workflow",
        "value_category": "RESEARCH",
        "baseline_minutes": 30.0,
    }
    create_resp = client.post("/api/workflows", json=payload)
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["name"] == "Test Workflow"
    assert created["value_category"] == "RESEARCH"
    workflow_id = created["id"]

    list_resp = client.get("/api/workflows")
    assert list_resp.status_code == 200
    ids = [w["id"] for w in list_resp.json()]
    assert workflow_id in ids


def test_get_workflow(client):
    """GET /api/workflows/{id} returns the correct workflow."""
    wf_list = client.get("/api/workflows").json()
    first_id = wf_list[0]["id"]
    resp = client.get(f"/api/workflows/{first_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == first_id


def test_get_nonexistent_workflow(client):
    """GET /api/workflows/{id} with unknown ID returns 404."""
    resp = client.get("/api/workflows/does-not-exist")
    assert resp.status_code == 404


def test_metrics_summary(client):
    """GET /api/metrics/summary returns the expected shape."""
    resp = client.get("/api/metrics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_net_roi_usd" in data
    assert "total_runs" in data
    assert "active_workflows" in data
    assert data["active_workflows"] >= 3  # seeds should be present


def test_runs_list_empty_ok(client):
    """GET /api/runs returns a list (may be empty) without error."""
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
