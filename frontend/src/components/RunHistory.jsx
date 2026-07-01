import { useEffect, useState } from 'react'
import { api } from '../api'

const STATUS_BADGE = {
  COMPLETED: 'bg-green-900 text-green-300',
  RUNNING: 'bg-yellow-900 text-yellow-300',
  FAILED: 'bg-red-900 text-red-300',
}

function durationStr(started, completed) {
  if (!completed) return '—'
  const ms = new Date(completed) - new Date(started)
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function shortId(id) {
  return id.slice(0, 8) + '…'
}

export default function RunHistory() {
  const [runs, setRuns] = useState([])
  const [page, setPage] = useState(1)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const PAGE_SIZE = 10

  useEffect(() => {
    setLoading(true)
    api.getRuns(page, PAGE_SIZE)
      .then(setRuns)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [page])

  if (error) return <p className="text-red-400 text-sm">Failed to load runs: {error}</p>

  if (!loading && runs.length === 0 && page === 1) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-lg">No runs yet</p>
        <p className="text-sm mt-1">Trigger a workflow to see run history here</p>
      </div>
    )
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-left">
              <th className="pb-3 pr-4 font-medium">Run ID</th>
              <th className="pb-3 pr-4 font-medium">Workflow</th>
              <th className="pb-3 pr-4 font-medium">Status</th>
              <th className="pb-3 pr-4 font-medium text-right">Net ROI</th>
              <th className="pb-3 pr-4 font-medium text-right">Token Cost</th>
              <th className="pb-3 pr-4 font-medium text-right">Duration</th>
              <th className="pb-3 font-medium text-right">Started</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="py-8 text-center text-gray-500">Loading…</td>
              </tr>
            ) : (
              runs.map((run) => {
                const badgeCls = STATUS_BADGE[run.status] || 'bg-gray-700 text-gray-300'
                return (
                  <tr key={run.id} className="border-b border-gray-800/50 hover:bg-gray-900/40">
                    <td className="py-2.5 pr-4 font-mono text-xs text-gray-400">{shortId(run.id)}</td>
                    <td className="py-2.5 pr-4 text-white">{run.workflow_name || '—'}</td>
                    <td className="py-2.5 pr-4">
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${badgeCls}`}>
                        {run.status}
                      </span>
                    </td>
                    <td className={`py-2.5 pr-4 text-right font-medium ${run.net_roi_usd >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ${run.net_roi_usd.toFixed(4)}
                    </td>
                    <td className="py-2.5 pr-4 text-right text-gray-300">${run.token_cost_usd.toFixed(6)}</td>
                    <td className="py-2.5 pr-4 text-right text-gray-400">
                      {durationStr(run.started_at, run.completed_at)}
                    </td>
                    <td className="py-2.5 text-right text-gray-500 text-xs">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex gap-3 justify-end">
        <button
          disabled={page === 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          className="px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40 rounded-lg text-gray-300 transition-colors"
        >
          ← Prev
        </button>
        <span className="text-xs text-gray-500 self-center">Page {page}</span>
        <button
          disabled={runs.length < PAGE_SIZE}
          onClick={() => setPage((p) => p + 1)}
          className="px-3 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-40 rounded-lg text-gray-300 transition-colors"
        >
          Next →
        </button>
      </div>
    </div>
  )
}
