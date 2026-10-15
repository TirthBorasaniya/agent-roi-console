import type { ConsoleApi } from './client'

// The public demo has no backend: scripts/export_demo_data.py writes each API
// response, derived from committed results files, into public/demo-data/
const DATA_ROOT = `${import.meta.env.BASE_URL}demo-data`

async function readJson<T>(name: string): Promise<T> {
  const response = await fetch(`${DATA_ROOT}/${name}.json`)
  if (!response.ok) {
    throw new Error(`demo data ${name} is not available (${response.status})`)
  }
  return response.json() as Promise<T>
}

export function createStaticApi(): ConsoleApi {
  return {
    o_read_only: true,
    meta: () => readJson('meta'),
    overview: () => readJson('overview'),
    workflows: () => readJson('workflows'),
    workflow: (id) => readJson(`workflow-${id}`),
    roi: (id) => readJson(`roi-${id}`),
    sensitivity: (id) => readJson(`sensitivity-${id}`),
    runs: (workflowId) => readJson(`runs-${workflowId}`),
    run: (runId) => readJson(`run-${runId}`),
    gatewayStats: () => readJson('gateway'),
    guardLog: () => readJson('guard'),
    triggerRun: () => Promise.reject(new Error('the public demo is read-only')),
  }
}
