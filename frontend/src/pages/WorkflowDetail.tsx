import { Fragment, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useApi } from '../api/client'
import type { RunDetail, RunSummary } from '../api/types'
import { Loading, Section, StatCard } from '../components/Layout'
import { SensitivityChart } from '../components/SensitivityChart'
import { pct, ratio, seconds, usd } from '../format'
import { useAsync } from '../useAsync'

function StepTrace({ runId }: { runId: string }) {
  const api = useApi()
  const { data, error } = useAsync<RunDetail>(() => api.run(runId), [api, runId])
  if (!data) return <Loading error={error} what="steps" />
  return (
    <div className="bg-gray-950 rounded-lg p-3 flex flex-col gap-2">
      <table className="w-full text-xs">
        <tbody>
          {data.steps.map((step, i) => (
            <tr key={`${step.step_id}-${i}`} className="border-t border-gray-800">
              <td className="py-1 font-mono">{step.step_id}</td>
              <td>{step.kind}</td>
              <td>{step.name}</td>
              <td className={step.status === 'completed' ? 'text-emerald-400' : 'text-red-400'}>{step.status}</td>
              <td>{seconds(step.latency_s)}</td>
              <td className="font-mono text-gray-400">{JSON.stringify(step.detail)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="text-xs">
        {data.validator_results.map((v) => (
          <li key={v.name} className={v.passed ? 'text-emerald-400' : 'text-red-400'}>
            {v.passed ? 'pass' : 'fail'} {v.name}: {v.detail}
          </li>
        ))}
      </ul>
    </div>
  )
}

function RunsTable({ runs }: { runs: RunSummary[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  if (runs.length === 0) return <p className="text-gray-500 text-sm">No runs yet.</p>
  return (
    <table className="w-full text-sm">
      <thead className="text-gray-400 text-left">
        <tr>
          <th className="py-2">Run</th>
          <th>Task</th>
          <th>Path</th>
          <th>Model</th>
          <th>Result</th>
          <th>Model cost</th>
          <th>Latency</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <Fragment key={run.run_id}>
            <tr className="border-t border-gray-800 cursor-pointer" onClick={() => setExpanded(expanded === run.run_id ? null : run.run_id)}>
              <td className="py-2 font-mono text-xs">{run.run_id}</td>
              <td>{run.task_id}</td>
              <td>{run.path}</td>
              <td>{run.model_config}</td>
              <td className={run.success ? 'text-emerald-400' : 'text-red-400'}>{run.success ? 'success' : run.failure_reason ?? 'failed'}</td>
              <td>{usd(run.llm_cost_usd, 5)}</td>
              <td>{seconds(run.latency_s)}</td>
            </tr>
            {expanded === run.run_id && (
              <tr>
                <td colSpan={7}>
                  <StepTrace runId={run.run_id} />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  )
}

export default function WorkflowDetail() {
  const { workflowId = '' } = useParams()
  const api = useApi()
  const workflow = useAsync(() => api.workflow(workflowId), [api, workflowId])
  const roi = useAsync(() => api.roi(workflowId), [api, workflowId])
  const sensitivity = useAsync(() => api.sensitivity(workflowId), [api, workflowId])
  const runs = useAsync(() => api.runs(workflowId), [api, workflowId])
  const [runMessage, setRunMessage] = useState<string | null>(null)

  if (!workflow.data) return <Loading error={workflow.error} what="workflow" />
  const wf = workflow.data
  const overall = roi.data?.overall
  const mix = Object.entries(wf.model_mix ?? {}).map(([model, calls]) => ({ model, calls }))

  const trigger = () => {
    setRunMessage('Running...')
    api
      .triggerRun(wf.workflow_id)
      .then((outcome) => setRunMessage(`${outcome.run_id}: ${outcome.success ? 'success' : outcome.failure_reason}`))
      .catch((e: Error) => setRunMessage(e.message))
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl text-white font-semibold">{wf.name}</h2>
          <p className="text-gray-400 text-sm">{wf.description}</p>
          <p className="text-gray-500 text-xs mt-1">
            Active path {wf.active_path}, guard {wf.guard_phase}. Baseline {wf.assumptions.minutes_per_task} min
            ({wf.assumptions.o_timed ? 'timed' : 'assumption'}), review {wf.assumptions.review_minutes_per_task} min, fix{' '}
            {wf.assumptions.fix_minutes_per_failure} min, {usd(wf.assumptions.labor_rate_usd_per_hour, 0)}/h.
          </p>
        </div>
        {!api.o_read_only && (
          <button onClick={trigger} disabled={wf.o_paused} className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm disabled:opacity-50">
            Run one task
          </button>
        )}
      </div>
      {runMessage && <p className="text-sm text-gray-300">{runMessage}</p>}
      {overall && overall.n_runs > 0 ? (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <StatCard label="Success" value={pct(overall.success?.rate)} sub={`break-even ${pct(overall.break_even_success_rate)}`} />
          <StatCard label="Net ROI" value={usd(overall.net_roi_usd)} sub={`${overall.n_runs} runs`} />
          <StatCard label="ROI ratio" value={ratio(overall.roi_ratio)} />
          <StatCard label="Cost per success" value={usd(overall.cost_per_success_usd)} sub={`model and tools ${usd(overall.machine_cost_per_success_usd, 4)}`} />
          <StatCard label="Break-even minutes" value={overall.break_even_minutes_per_task?.toFixed(1) ?? 'n/a'} />
        </div>
      ) : (
        <Loading error={roi.error} what="ROI" />
      )}
      <Section title="Sensitivity">
        {sensitivity.data ? <SensitivityChart view={sensitivity.data} /> : <Loading error={sensitivity.error} what="sensitivity" />}
      </Section>
      <Section title="Model mix">
        {mix.length === 0 ? (
          <p className="text-gray-500 text-sm">No model calls recorded.</p>
        ) : (
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={mix}>
                <XAxis dataKey="model" stroke="#9ca3af" />
                <YAxis stroke="#9ca3af" />
                <Tooltip />
                <Bar dataKey="calls" fill="#60a5fa" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Section>
      <Section title="Runs">{runs.data ? <RunsTable runs={runs.data} /> : <Loading error={runs.error} what="runs" />}</Section>
    </div>
  )
}
