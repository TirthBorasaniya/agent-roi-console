import { useApi } from '../api/client'
import { Loading, Section } from '../components/Layout'
import { usd } from '../format'
import { useAsync } from '../useAsync'

export default function GuardLog() {
  const api = useApi()
  const { data, error } = useAsync(() => api.guardLog(), [api])
  if (!data) return <Loading error={error} what="guard log" />
  return (
    <Section title="Guard actions">
      {data.length === 0 ? (
        <p className="text-gray-500 text-sm">The guard has not acted on any workflow.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-gray-400 text-left">
            <tr>
              <th className="py-2">Time</th>
              <th>Workflow</th>
              <th>Action</th>
              <th>Path</th>
              <th>Window runs</th>
              <th>Net ROI</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {data.map((a) => (
              <tr key={a.id} className="border-t border-gray-800">
                <td className="py-2 text-xs">{a.created_at}</td>
                <td>{a.workflow_id}</td>
                <td className={a.action === 'pause' ? 'text-red-400' : 'text-amber-400'}>{a.action}</td>
                <td>{a.from_path === a.to_path ? a.to_path : `${a.from_path} to ${a.to_path}`}</td>
                <td>{a.window_runs}</td>
                <td>{usd(a.net_roi_usd)}</td>
                <td className="text-gray-400">{a.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}
