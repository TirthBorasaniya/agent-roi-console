// Response types of the console API (backend/src/roiconsole/api). Every number the
// dashboard shows comes from these responses; the client defines no constants.

export interface RateSummary {
  successes: number
  trials: number
  rate: number | null
  ci95_low: number
  ci95_high: number
}

export interface Meta {
  version: string
  connector_mode: string
  o_demo: boolean
  results: Record<string, string | null>
  snapshot_date?: string
  o_static?: boolean
}

export interface BudgetBurn {
  spent_today_usd: number
  daily_budget_usd: number
  fraction: number | null
}

export interface OverviewItem {
  workflow_id: string
  name: string
  active_path: string
  guard_phase: string
  n_runs: number
  success: RateSummary | null
  net_roi_usd: number | null
  roi_ratio: number | null
  cost_per_success_usd: number | null
  machine_cost_per_success_usd: number | null
  break_even_success_rate: number | null
  budget: BudgetBurn | null
  o_baseline_timed: boolean
}

export interface Assumptions {
  minutes_per_task: number
  minutes_source: string
  o_timed: boolean
  labor_rate_usd_per_hour: number
  review_minutes_per_task: number
  fix_minutes_per_failure: number
}

export interface WorkflowStep {
  step_id: string
  type: string
  tool: string | null
  step_class: string | null
}

export interface WorkflowView {
  workflow_id: string
  name: string
  description: string
  value_category: string
  default_path: string
  active_path: string
  guard_phase: string
  o_paused: boolean
  steps: WorkflowStep[]
  assumptions: Assumptions
  model_mix?: Record<string, number>
}

export interface RoiSummary {
  n_runs: number
  success?: RateSummary
  value_usd?: number
  cost_usd?: number
  net_roi_usd?: number
  net_roi_per_task_usd?: number
  roi_ratio?: number | null
  cost_per_success_usd?: number | null
  machine_cost_per_success_usd?: number | null
  break_even_success_rate?: number | null
  break_even_minutes_per_task?: number | null
}

export interface RoiView {
  workflow_id: string
  window_hours: number
  overall: RoiSummary
  by_path: Record<string, RoiSummary>
}

export interface SensitivityPoint {
  minutes_multiplier: number
  minutes_per_task: number
  labor_rate_usd_per_hour: number
  net_roi_per_task_usd: number | null
  roi_ratio: number | null
  break_even_minutes_per_task: number | null
  break_even_success_rate: number | null
}

export interface SensitivityView {
  workflow_id: string
  baseline_minutes_per_task: number
  labor_rate_usd_per_hour: number
  grid: SensitivityPoint[]
}

export interface ValidatorResult {
  name: string
  passed: boolean
  detail: string
}

export interface RunSummary {
  run_id: string
  workflow_id: string
  task_id: string | null
  path: string
  model_config: string
  trigger: string
  status: string
  success: number | boolean | null
  failure_reason: string | null
  validator_results: ValidatorResult[]
  llm_cost_usd: number
  latency_s: number | null
  started_at: string
}

export interface StepTraceRow {
  step_id: string
  kind: string
  name: string
  status: string
  latency_s: number | null
  detail: Record<string, unknown>
}

export interface RunDetail extends RunSummary {
  steps: StepTraceRow[]
  tool_calls: { tool: string; server: string; status: string; step_id: string }[]
  llm_calls: { model: string; cost_usd: number; cache: string; fallback_depth: number; step_id: string }[]
}

export interface GatewayStats {
  window_hours: number
  calls: number
  outcomes: Record<string, number>
  fallback_depth_distribution: Record<string, number>
  cache: Record<string, number>
  cache_hit_rate: number | null
  by_model: Record<string, { calls: number; cost_usd: number; input_tokens: number; output_tokens: number }>
  total_cost_usd: number
  breakers: Record<string, string> | null
}

export interface GuardAction {
  id: number
  workflow_id: string
  action: string
  from_path: string | null
  to_path: string | null
  window_runs: number
  net_roi_usd: number | null
  detail: string | null
  created_at: string
}

export interface RunOutcome {
  run_id: string
  success: boolean
  failure_reason: string | null
}
