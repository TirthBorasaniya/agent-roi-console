import { useEffect, useState } from 'react'
import { api } from '../api'

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-gray-900 rounded-xl p-6 flex flex-col gap-1 border border-gray-800">
      <span className="text-xs text-gray-400 uppercase tracking-wider">{label}</span>
      <span className="text-3xl font-semibold text-white">{value}</span>
      {sub && <span className="text-xs text-gray-500">{sub}</span>}
    </div>
  )
}

export default function MetricsSummary() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getMetricsSummary()
      .then(setData)
      .catch((e) => setError(e.message))
  }, [])

  if (error) return <p className="text-red-400 text-sm">Failed to load metrics: {error}</p>
  if (!data) return <p className="text-gray-500 text-sm">Loading metrics…</p>

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <StatCard
        label="Total ROI Saved"
        value={`$${data.total_net_roi_usd.toFixed(2)}`}
        sub="net of token costs"
      />
      <StatCard
        label="Automations Run"
        value={data.total_runs.toLocaleString()}
        sub="completed runs"
      />
      <StatCard
        label="Avg Cost / Run"
        value={`$${data.avg_token_cost_usd.toFixed(4)}`}
        sub="token cost only"
      />
      <StatCard
        label="Active Workflows"
        value={data.active_workflows}
        sub="registered automations"
      />
    </div>
  )
}
