"""Pydantic request and response models for the Agent ROI Console API."""

# ============= Standard Library =============
from typing import Optional

# ============= Third-Party =============
from pydantic import BaseModel, Field

# ============= Constants =============
VALUE_CATEGORIES = [
    "RESEARCH",
    "COMMUNICATION",
    "DATA_ENTRY",
    "SUMMARIZATION",
    "COORDINATION",
]


# ============= Workflow Models =============

class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    value_category: str
    baseline_minutes: float = Field(gt=0)


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    value_category: str
    baseline_minutes: float
    created_at: str


# ============= Run Models =============

class RunTriggerRequest(BaseModel):
    input_payload: Optional[str] = None


class ToolUsageRecord(BaseModel):
    id: str
    run_id: str
    tool_name: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    called_at: str


class RunResponse(BaseModel):
    id: str
    workflow_id: str
    workflow_name: Optional[str] = None
    status: str
    trigger: str
    input_payload: Optional[str]
    output_summary: Optional[str]
    input_tokens: int
    output_tokens: int
    token_cost_usd: float
    net_roi_usd: float
    tools_used: Optional[str]
    started_at: str
    completed_at: Optional[str]
    tool_usage: list[ToolUsageRecord] = []


# ============= Metrics Models =============

class MetricsSummary(BaseModel):
    total_net_roi_usd: float
    total_runs: int
    avg_token_cost_usd: float
    active_workflows: int


class ROIByCategory(BaseModel):
    value_category: str
    total_net_roi_usd: float
    run_count: int


class CostByTool(BaseModel):
    tool_name: str
    total_cost_usd: float
    call_count: int
    total_input_tokens: int
    total_output_tokens: int


class TimelinePoint(BaseModel):
    date: str
    run_count: int
    total_net_roi_usd: float
