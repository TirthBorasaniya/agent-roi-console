const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function apiFetch(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json()
}

export const api = {
  getWorkflows: () => apiFetch('/api/workflows'),
  createWorkflow: (data) => apiFetch('/api/workflows', { method: 'POST', body: JSON.stringify(data) }),
  getWorkflow: (id) => apiFetch(`/api/workflows/${id}`),
  triggerRun: (id, payload = {}) =>
    apiFetch(`/api/workflows/${id}/run`, { method: 'POST', body: JSON.stringify(payload) }),

  getRuns: (page = 1, pageSize = 20) => apiFetch(`/api/runs?page=${page}&page_size=${pageSize}`),
  getRun: (id) => apiFetch(`/api/runs/${id}`),

  getMetricsSummary: () => apiFetch('/api/metrics/summary'),
  getROIByCategory: () => apiFetch('/api/metrics/roi-by-category'),
  getCostByTool: () => apiFetch('/api/metrics/cost-by-tool'),
  getTimeline: () => apiFetch('/api/metrics/timeline'),
}
