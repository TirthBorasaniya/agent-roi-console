import type { ConsoleApi } from '../api/client'
import type { OverviewItem, RoiView, RunDetail, RunSummary, SensitivityView, WorkflowView } from '../api/types'

// fixtures for component tests only; the app never defines numbers client-side
export const overviewFixture: OverviewItem[] = [
  {
    workflow_id: 'crm_pipeline',
    name: 'CRM Pipeline Summary',
    active_path: 'pipeline',
    guard_phase: 'normal',
    n_runs: 10,
    success: { successes: 9, trials: 10, rate: 0.9, ci95_low: 0.6, ci95_high: 0.98 },
    net_roi_usd: 42.5,
    roi_ratio: 3.1,
    cost_per_success_usd: 2.25,
    machine_cost_per_success_usd: 0.004,
    break_even_success_rate: 0.66,
    budget: { spent_today_usd: 0.12, daily_budget_usd: 5, fraction: 0.024 },
    o_baseline_timed: false,
  },
  {
    workflow_id: 'slack_digest',
    name: 'Slack Channel Digest',
    active_path: 'pipeline',
    guard_phase: 'paused',
    n_runs: 40,
    success: { successes: 4, trials: 40, rate: 0.1, ci95_low: 0.04, ci95_high: 0.23 },
    net_roi_usd: -310.2,
    roi_ratio: 0.2,
    cost_per_success_usd: 90,
    machine_cost_per_success_usd: 0.02,
    break_even_success_rate: 0.7,
    budget: { spent_today_usd: 0.4, daily_budget_usd: 5, fraction: 0.08 },
    o_baseline_timed: true,
  },
]

export const workflowFixture: WorkflowView = {
  workflow_id: 'crm_pipeline',
  name: 'CRM Pipeline Summary',
  description: 'List open opportunities and post a summary.',
  value_category: 'COORDINATION',
  default_path: 'pipeline',
  active_path: 'pipeline',
  guard_phase: 'normal',
  o_paused: false,
  steps: [],
  assumptions: {
    minutes_per_task: 12,
    minutes_source: 'Assumption',
    o_timed: false,
    labor_rate_usd_per_hour: 55,
    review_minutes_per_task: 2,
    fix_minutes_per_failure: 15,
  },
  model_mix: { 'groq/openai/gpt-oss-20b': 8 },
}

export const roiFixture: RoiView = {
  workflow_id: 'crm_pipeline',
  window_hours: 720,
  overall: {
    n_runs: 10,
    success: { successes: 9, trials: 10, rate: 0.9, ci95_low: 0.6, ci95_high: 0.98 },
    net_roi_usd: 42.5,
    roi_ratio: 3.1,
    cost_per_success_usd: 2.25,
    machine_cost_per_success_usd: 0.004,
    break_even_success_rate: 0.66,
    break_even_minutes_per_task: 2.4,
  },
  by_path: {},
}

export const sensitivityFixture: SensitivityView = {
  workflow_id: 'crm_pipeline',
  baseline_minutes_per_task: 12,
  labor_rate_usd_per_hour: 55,
  grid: [3, 6, 12, 18].flatMap((minutes) =>
    [25, 55, 100].map((rate) => ({
      minutes_multiplier: minutes / 12,
      minutes_per_task: minutes,
      labor_rate_usd_per_hour: rate,
      net_roi_per_task_usd: (minutes - 4) * (rate / 60),
      roi_ratio: 1,
      break_even_minutes_per_task: 4,
      break_even_success_rate: 0.5,
    })),
  ),
}

export const runsFixture: RunSummary[] = [
  {
    run_id: 'run_abc',
    workflow_id: 'crm_pipeline',
    task_id: 'crm_pipeline_004',
    path: 'pipeline',
    model_config: 'auto',
    trigger: 'api',
    status: 'COMPLETED',
    success: 1,
    failure_reason: null,
    validator_results: [],
    llm_cost_usd: 0.0012,
    latency_s: 3.2,
    started_at: '2026-10-14T00:00:00Z',
  },
]

export const runDetailFixture: RunDetail = {
  ...runsFixture[0],
  validator_results: [{ name: 'slack_message_posted', passed: true, detail: '1 bot message(s)' }],
  steps: [{ step_id: 'fetch_opportunities', kind: 'tool', name: 'crm.list_opportunities', status: 'completed', latency_s: 0.1, detail: {} }],
  tool_calls: [],
  llm_calls: [],
}

export function mockApi(overrides: Partial<ConsoleApi> = {}): ConsoleApi {
  return {
    o_read_only: false,
    meta: async () => ({ version: '2.0.0', connector_mode: 'demo', o_demo: true, results: {} }),
    overview: async () => overviewFixture,
    workflows: async () => [workflowFixture],
    workflow: async () => workflowFixture,
    roi: async () => roiFixture,
    sensitivity: async () => sensitivityFixture,
    runs: async () => runsFixture,
    run: async () => runDetailFixture,
    gatewayStats: async () => ({
      window_hours: 720,
      calls: 0,
      outcomes: {},
      fallback_depth_distribution: {},
      cache: {},
      cache_hit_rate: null,
      by_model: {},
      total_cost_usd: 0,
      breakers: null,
    }),
    guardLog: async () => [],
    triggerRun: async () => ({ run_id: 'run_new', success: true, failure_reason: null }),
    ...overrides,
  }
}
