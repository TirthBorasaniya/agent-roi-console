import type {
  CostByTool,
  MetricsSummary,
  ROIByCategory,
  Run,
  RunTriggerRequest,
  TimelinePoint,
  Workflow,
  WorkflowCreate,
} from './types'

// default to same-origin: in production FastAPI serves this bundle itself, so
// relative paths keep the deployment to one service and make CORS moot. the
// Vite dev server proxies /api to the backend, so dev needs no override either
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  getWorkflows: (): Promise<Workflow[]> => apiFetch('/api/workflows'),
  createWorkflow: (data: WorkflowCreate): Promise<Workflow> =>
    apiFetch('/api/workflows', { method: 'POST', body: JSON.stringify(data) }),
  getWorkflow: (id: string): Promise<Workflow> => apiFetch(`/api/workflows/${id}`),
  triggerRun: (id: string, payload: RunTriggerRequest = {}): Promise<Run> =>
    apiFetch(`/api/workflows/${id}/run`, { method: 'POST', body: JSON.stringify(payload) }),

  getRuns: (page = 1, pageSize = 20): Promise<Run[]> =>
    apiFetch(`/api/runs?page=${page}&page_size=${pageSize}`),
  getRun: (id: string): Promise<Run> => apiFetch(`/api/runs/${id}`),

  getMetricsSummary: (): Promise<MetricsSummary> => apiFetch('/api/metrics/summary'),
  getROIByCategory: (): Promise<ROIByCategory[]> => apiFetch('/api/metrics/roi-by-category'),
  getCostByTool: (): Promise<CostByTool[]> => apiFetch('/api/metrics/cost-by-tool'),
  getTimeline: (): Promise<TimelinePoint[]> => apiFetch('/api/metrics/timeline'),
}
