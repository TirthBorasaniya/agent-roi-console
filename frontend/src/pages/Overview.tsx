import { Link } from 'react-router-dom'
import { useApi } from '../api/client'
import { Loading, Section, StatCard } from '../components/Layout'
import { pct, ratio, usd } from '../format'
import { useAsync } from '../useAsync'

export default function Overview() {
  const api = useApi()
  const { data, error } = useAsync(() => api.overview(), [api])
  if (!data) return <Loading error={error} what="overview" />
  const totalNet = data.reduce((sum, item) => sum + (item.net_roi_usd ?? 0), 0)
  const totalRuns = data.reduce((sum, item) => sum + item.n_runs, 0)
  const paused = data.filter((item) => item.guard_phase === 'paused').length
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Net ROI" value={usd(totalNet)} sub="value minus model, tool, review, and fix cost" />
        <StatCard label="Runs" value={totalRuns} />
        <StatCard label="Workflows" value={data.length} />
        <StatCard label="Paused by guard" value={paused} />
      </div>
      <Section title="Workflows">
        <table className="w-full text-sm">
          <thead className="text-gray-400 text-left">
            <tr>
              <th className="py-2">Workflow</th>
              <th>Path</th>
              <th>Guard</th>
              <th>Runs</th>
              <th>Success (95% CI)</th>
              <th>Break-even success</th>
              <th>Net ROI</th>
              <th>ROI ratio</th>
              <th>Cost per success</th>
              <th>Budget today</th>
            </tr>
          </thead>
          <tbody>
            {data.map((item) => (
              <tr key={item.workflow_id} className="border-t border-gray-800">
                <td className="py-2">
                  <Link to={`/workflows/${item.workflow_id}`} className="text-blue-400 hover:underline">
                    {item.name}
                  </Link>
                  {!item.o_baseline_timed && <span className="ml-2 text-xs text-amber-400">baseline assumed</span>}
                </td>
                <td>{item.active_path}</td>
                <td className={item.guard_phase === 'normal' ? 'text-gray-300' : 'text-amber-400'}>{item.guard_phase}</td>
                <td>{item.n_runs}</td>
                <td>
                  {item.success ? `${pct(item.success.rate)} (${pct(item.success.ci95_low)} to ${pct(item.success.ci95_high)})` : 'n/a'}
                </td>
                <td>{pct(item.break_even_success_rate)}</td>
                <td className={(item.net_roi_usd ?? 0) < 0 ? 'text-red-400' : 'text-emerald-400'}>{usd(item.net_roi_usd)}</td>
                <td>{ratio(item.roi_ratio)}</td>
                <td>{usd(item.cost_per_success_usd)}</td>
                <td>
                  {item.budget ? `${usd(item.budget.spent_today_usd)} of ${usd(item.budget.daily_budget_usd)}` : 'n/a'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  )
}
