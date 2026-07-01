import { useEffect, useState } from 'react'
import { api } from '../api'

const CATEGORY_COLORS = {
  RESEARCH: 'bg-blue-900 text-blue-300',
  COMMUNICATION: 'bg-green-900 text-green-300',
  DATA_ENTRY: 'bg-yellow-900 text-yellow-300',
  SUMMARIZATION: 'bg-purple-900 text-purple-300',
  COORDINATION: 'bg-orange-900 text-orange-300',
}

const STATUS_COLORS = {
  COMPLETED: 'text-green-400',
  RUNNING: 'text-yellow-400',
  FAILED: 'text-red-400',
}

function Spinner() {
  return (
    <span className="inline-block w-4 h-4 border-2 border-gray-600 border-t-blue-400 rounded-full animate-spin" />
  )
}

export default function WorkflowList({ onRunComplete }) {
  const [workflows, setWorkflows] = useState([])
  const [runs, setRuns] = useState([])
  const [runningIds, setRunningIds] = useState(new Set())
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const fetchData = () =>
    Promise.all([api.getWorkflows(), api.getRuns(1, 100)])
      .then(([wfs, rns]) => {
        setWorkflows(wfs)
        setRuns(rns)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))

  useEffect(() => { fetchData() }, [])

  const handleRun = async (workflowId) => {
    setRunningIds((prev) => new Set([...prev, workflowId]))
    try {
      await api.triggerRun(workflowId)
      await fetchData()
      onRunComplete?.()
    } catch (e) {
      setError(`Run failed: ${e.message}`)
    } finally {
      setRunningIds((prev) => {
        const next = new Set(prev)
        next.delete(workflowId)
        return next
      })
    }
  }

  if (error) return <p className="text-red-400 text-sm">Error: {error}</p>
  if (loading) return <p className="text-gray-500 text-sm">Loading workflows…</p>

  if (workflows.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-lg">No workflows defined</p>
        <p className="text-sm mt-1">Use the API or database to create workflows</p>
      </div>
    )
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {workflows.map((wf) => {
        const wfRuns = runs.filter((r) => r.workflow_id === wf.id)
        const lastRun = wfRuns[0]
        const isRunning = runningIds.has(wf.id)
        const catCls = CATEGORY_COLORS[wf.value_category] || 'bg-gray-700 text-gray-300'

        return (
          <div key={wf.id} className="bg-gray-900 border border-gray-800 rounded-xl p-5 flex flex-col gap-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="font-semibold text-white text-sm">{wf.name}</h3>
                {wf.description && (
                  <p className="text-xs text-gray-400 mt-0.5">{wf.description}</p>
                )}
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap ${catCls}`}>
                {wf.value_category}
              </span>
            </div>

            <div className="flex gap-4 text-xs text-gray-400">
              <span>Baseline: <span className="text-gray-200">{wf.baseline_minutes} min</span></span>
              <span>Runs: <span className="text-gray-200">{wfRuns.length}</span></span>
            </div>

            {lastRun && (
              <div className="text-xs border-t border-gray-800 pt-2 flex gap-3">
                <span>
                  Last:{' '}
                  <span className={STATUS_COLORS[lastRun.status] || 'text-gray-400'}>
                    {lastRun.status}
                  </span>
                </span>
                {lastRun.status === 'COMPLETED' && (
                  <span>
                    ROI:{' '}
                    <span className={lastRun.net_roi_usd >= 0 ? 'text-green-400' : 'text-red-400'}>
                      ${lastRun.net_roi_usd.toFixed(4)}
                    </span>
                  </span>
                )}
              </div>
            )}

            <button
              onClick={() => handleRun(wf.id)}
              disabled={isRunning}
              className="mt-auto flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:cursor-not-allowed text-white text-sm font-medium py-2 rounded-lg transition-colors"
            >
              {isRunning ? (
                <>
                  <Spinner />
                  Running…
                </>
              ) : (
                'Run'
              )}
            </button>
          </div>
        )
      })}
    </div>
  )
}
