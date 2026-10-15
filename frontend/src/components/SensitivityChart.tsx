import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { SensitivityView } from '../api/types'
import { usd } from '../format'

const LINE_COLORS = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24']

// net ROI per task against minutes per task, one line per labor rate; the y = 0 line
// marks break-even and the vertical line marks the baseline assumption
export function SensitivityChart({ view }: { view: SensitivityView }) {
  if (view.grid.length === 0) {
    return <p className="text-gray-500 text-sm">No completed runs yet, so there is nothing to vary.</p>
  }
  const rates = [...new Set(view.grid.map((g) => g.labor_rate_usd_per_hour))].sort((a, b) => a - b)
  const minutes = [...new Set(view.grid.map((g) => g.minutes_per_task))].sort((a, b) => a - b)
  const rows = minutes.map((m) => {
    const row: Record<string, number | null> = { minutes: m }
    for (const rate of rates) {
      const point = view.grid.find((g) => g.minutes_per_task === m && g.labor_rate_usd_per_hour === rate)
      row[`r${rate}`] = point?.net_roi_per_task_usd ?? null
    }
    return row
  })
  return (
    <div className="h-72" data-testid="sensitivity-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="minutes" stroke="#9ca3af" label={{ value: 'minutes per task', position: 'insideBottom', offset: -4, fill: '#9ca3af' }} />
          <YAxis stroke="#9ca3af" tickFormatter={(v: number) => usd(v, 0)} />
          <Tooltip formatter={(v: number) => usd(v)} />
          <Legend />
          <ReferenceLine y={0} stroke="#ef4444" label={{ value: 'break-even', fill: '#ef4444', position: 'insideTopLeft' }} />
          <ReferenceLine x={view.baseline_minutes_per_task} stroke="#9ca3af" strokeDasharray="4 4" label={{ value: 'baseline', fill: '#9ca3af' }} />
          {rates.map((rate, i) => (
            <Line key={rate} type="monotone" dataKey={`r${rate}`} name={`$${rate}/h`} stroke={LINE_COLORS[i % LINE_COLORS.length]} dot />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
