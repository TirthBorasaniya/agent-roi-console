import type { ConsoleApi } from './client'

// same-origin by default: FastAPI serves the built bundle, and the Vite dev server
// proxies /api to the backend
const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''

async function getJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }
  return response.json() as Promise<T>
}

export function createHttpApi(): ConsoleApi {
  return {
    o_read_only: false,
    meta: () => getJson('/api/meta'),
    overview: () => getJson('/api/overview'),
    workflows: () => getJson('/api/workflows'),
    workflow: (id) => getJson(`/api/workflows/${encodeURIComponent(id)}`),
    roi: (id) => getJson(`/api/workflows/${encodeURIComponent(id)}/roi`),
    sensitivity: (id) => getJson(`/api/workflows/${encodeURIComponent(id)}/sensitivity`),
    runs: (workflowId) => getJson(`/api/runs?workflow_id=${encodeURIComponent(workflowId)}&page_size=50`),
    run: (runId) => getJson(`/api/runs/${encodeURIComponent(runId)}`),
    gatewayStats: () => getJson('/api/gateway/stats'),
    guardLog: () => getJson('/api/guard/log'),
    triggerRun: (workflowId) =>
      getJson(`/api/workflows/${encodeURIComponent(workflowId)}/run`, { method: 'POST', body: '{}' }),
  }
}
