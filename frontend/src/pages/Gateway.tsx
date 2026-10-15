import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useApi } from '../api/client'
import { Loading, Section, StatCard } from '../components/Layout'
import { pct, usd } from '../format'
import { useAsync } from '../useAsync'

export default function Gateway() {
  const api = useApi()
  const { data, error } = useAsync(() => api.gatewayStats(), [api])
  if (!data) return <Loading error={error} what="gateway statistics" />
  const depth = Object.entries(data.fallback_depth_distribution).map(([d, n]) => ({ depth: d, requests: n }))
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Calls" value={data.calls} />
        <StatCard label="Cache hit rate" value={pct(data.cache_hit_rate)} sub={Object.entries(data.cache).map(([k, v]) => `${k} ${v}`).join(', ')} />
        <StatCard label="Model spend" value={usd(data.total_cost_usd, 4)} />
        <StatCard label="Outcomes" value={Object.keys(data.outcomes).length} sub={Object.entries(data.outcomes).map(([k, v]) => `${k} ${v}`).join(', ')} />
      </div>
      <Section title="Fallback depth">
        {depth.length === 0 ? (
          <p className="text-gray-500 text-sm">No successful calls yet.</p>
        ) : (
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={depth}>
                <XAxis dataKey="depth" stroke="#9ca3af" />
                <YAxis stroke="#9ca3af" />
                <Tooltip />
                <Bar dataKey="requests" fill="#34d399" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Section>
      <Section title="Circuit breakers">
        {data.breakers ? (
          <ul className="text-sm">
            {Object.entries(data.breakers).map(([model, state]) => (
              <li key={model}>
                <span className="font-mono">{model}</span>: <span className={state === 'closed' ? 'text-emerald-400' : 'text-amber-400'}>{state}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-gray-500 text-sm">Gateway not reachable, or no breaker state in this snapshot.</p>
        )}
      </Section>
      <Section title="By model">
        <table className="w-full text-sm">
          <thead className="text-gray-400 text-left">
            <tr>
              <th className="py-2">Model</th>
              <th>Calls</th>
              <th>Input tokens</th>
              <th>Output tokens</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.by_model).map(([model, m]) => (
              <tr key={model} className="border-t border-gray-800">
                <td className="py-2 font-mono">{model}</td>
                <td>{m.calls}</td>
                <td>{m.input_tokens}</td>
                <td>{m.output_tokens}</td>
                <td>{usd(m.cost_usd, 5)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  )
}
