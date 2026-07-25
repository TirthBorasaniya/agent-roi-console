// Shared API types mirroring the backend Pydantic response models
// (see backend/models.py). Keep these in sync with the API contract.

export type ValueCategory =
  | 'RESEARCH'
  | 'COMMUNICATION'
  | 'DATA_ENTRY'
  | 'SUMMARIZATION'
  | 'COORDINATION'

export type RunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED'

export interface Workflow {
  id: string
  name: string
  description: string | null
  value_category: string
  baseline_minutes: number
  created_at: string
}

export interface WorkflowCreate {
  name: string
  description?: string | null
  value_category: string
  baseline_minutes: number
}

export interface ToolUsageRecord {
  id: string
  run_id: string
  tool_name: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  latency_ms: number
  called_at: string
}

export interface Run {
  id: string
  workflow_id: string
  workflow_name: string | null
  status: string
  trigger: string
  input_payload: string | null
  output_summary: string | null
  input_tokens: number
  output_tokens: number
  token_cost_usd: number
  net_roi_usd: number
  tools_used: string | null
  started_at: string
  completed_at: string | null
  tool_usage: ToolUsageRecord[]
}

export interface MetricsSummary {
  total_net_roi_usd: number
  total_runs: number
  avg_token_cost_usd: number
  active_workflows: number
}

export interface ROIByCategory {
  value_category: string
  total_net_roi_usd: number
  run_count: number
}

export interface CostByTool {
  tool_name: string
  total_cost_usd: number
  call_count: number
  total_input_tokens: number
  total_output_tokens: number
}

export interface TimelinePoint {
  date: string
  run_count: number
  total_net_roi_usd: number
}

// Payload accepted by the workflow trigger endpoint
export interface RunTriggerRequest {
  input_payload?: string | null
}
