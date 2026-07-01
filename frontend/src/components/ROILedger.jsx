import { useEffect, useState } from 'react'
import { api } from '../api'

const CATEGORY_COLORS = {
  RESEARCH: 'bg-blue-900 text-blue-300',
  COMMUNICATION: 'bg-green-900 text-green-300',
  DATA_ENTRY: 'bg-yellow-900 text-yellow-300',
  SUMMARIZATION: 'bg-purple-900 text-purple-300',
  COORDINATION: 'bg-orange-900 text-orange-300',
}

function CategoryBadge({ category }) {
  const cls = CATEGORY_COLORS[category] || 'bg-gray-700 text-gray-300'
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cls}`}>
      {category}
    </span>
  )
}

export default function ROILedger() {
  const [workflows, setWorkflows] = useState([])
  const [runs, setRuns] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.getWorkflows(), api.getRuns(1, 100)])
      .then(([wfs, rns]) => {
        setWorkflows(wfs)
        setRuns(rns)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (error) return <p className="text-red-400 text-sm">Failed to load ledger: {error}</p>
  if (loading) return <p className="text-gray-500 text-sm">Loading ROI ledger…</p>

  if (workflows.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-lg">No workflows yet</p>
        <p className="text-sm mt-1">Create a workflow to start tracking ROI</p>
      </div>
    )
  }

  const completedRuns = runs.filter((r) => r.status === 'COMPLETED')

  const rows = workflows.map((wf) => {
    const wfRuns = completedRuns.filter((r) => r.workflow_id === wf.id)
    const totalRoi = wfRuns.reduce((acc, r) => acc + r.net_roi_usd, 0)
    const avgCost = wfRuns.length > 0
      ? wfRuns.reduce((acc, r) => acc + r.token_cost_usd, 0) / wfRuns.length
      : 0
    return { ...wf, runCount: wfRuns.length, totalRoi, avgCost }
  })

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800 text-gray-400 text-left">
            <th className="pb-3 pr-4 font-medium">Workflow</th>
            <th className="pb-3 pr-4 font-medium">Category</th>
            <th className="pb-3 pr-4 font-medium text-right">Baseline (min)</th>
            <th className="pb-3 pr-4 font-medium text-right">Runs</th>
            <th className="pb-3 pr-4 font-medium text-right">Total ROI</th>
            <th className="pb-3 font-medium text-right">Avg Token Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-gray-800/50 hover:bg-gray-900/40">
              <td className="py-3 pr-4 text-white font-medium">{row.name}</td>
              <td className="py-3 pr-4">
                <CategoryBadge category={row.value_category} />
              </td>
              <td className="py-3 pr-4 text-right text-gray-300">{row.baseline_minutes}</td>
              <td className="py-3 pr-4 text-right text-gray-300">{row.runCount}</td>
              <td className={`py-3 pr-4 text-right font-medium ${row.totalRoi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                ${row.totalRoi.toFixed(4)}
              </td>
              <td className="py-3 text-right text-gray-300">${row.avgCost.toFixed(6)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
