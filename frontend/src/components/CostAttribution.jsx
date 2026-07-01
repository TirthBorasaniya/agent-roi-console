import { useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell,
} from 'recharts'
import { api } from '../api'

const TOOL_COLORS = {
  post_slack_message: '#38bdf8',
  read_slack_channel: '#0ea5e9',
  read_notion_page: '#a78bfa',
  create_notion_page: '#7c3aed',
  search_notion: '#c4b5fd',
  crm_search_contacts: '#34d399',
  crm_create_note: '#10b981',
  crm_list_opportunities: '#6ee7b7',
}

function getColor(toolName) {
  return TOOL_COLORS[toolName] || '#6b7280'
}

export default function CostAttribution() {
  const [data, setData] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getCostByTool()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (error) return <p className="text-red-400 text-sm">Failed to load cost data: {error}</p>
  if (loading) return <p className="text-gray-500 text-sm">Loading cost breakdown…</p>

  if (data.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-lg">No tool usage data yet</p>
        <p className="text-sm mt-1">Trigger a workflow run to see cost attribution</p>
      </div>
    )
  }

  const chartData = data.map((d) => ({
    name: d.tool_name.replace(/_/g, ' '),
    raw_name: d.tool_name,
    cost: parseFloat(d.total_cost_usd.toFixed(6)),
    calls: d.call_count,
  }))

  return (
    <div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="name"
            tick={{ fill: '#9ca3af', fontSize: 11 }}
            axisLine={{ stroke: '#374151' }}
          />
          <YAxis
            yAxisId="cost"
            orientation="left"
            tick={{ fill: '#9ca3af', fontSize: 11 }}
            axisLine={{ stroke: '#374151' }}
            tickFormatter={(v) => `$${v.toFixed(4)}`}
          />
          <YAxis
            yAxisId="calls"
            orientation="right"
            tick={{ fill: '#9ca3af', fontSize: 11 }}
            axisLine={{ stroke: '#374151' }}
          />
          <Tooltip
            contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: '8px' }}
            labelStyle={{ color: '#f9fafb' }}
            itemStyle={{ color: '#d1d5db' }}
            formatter={(value, name) => name === 'cost' ? [`$${value.toFixed(6)}`, 'Token Cost'] : [value, 'Calls']}
          />
          <Legend
            wrapperStyle={{ color: '#9ca3af', fontSize: 12 }}
          />
          <Bar yAxisId="cost" dataKey="cost" name="Token Cost (USD)" radius={[4, 4, 0, 0]}>
            {chartData.map((entry) => (
              <Cell key={entry.raw_name} fill={getColor(entry.raw_name)} />
            ))}
          </Bar>
          <Bar yAxisId="calls" dataKey="calls" name="Call Count" fill="#374151" radius={[4, 4, 0, 0]} opacity={0.6} />
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-xs text-gray-400">
          <thead>
            <tr className="border-b border-gray-800">
              <th className="pb-2 text-left font-medium">Tool</th>
              <th className="pb-2 text-right font-medium">Calls</th>
              <th className="pb-2 text-right font-medium">Input Tokens</th>
              <th className="pb-2 text-right font-medium">Output Tokens</th>
              <th className="pb-2 text-right font-medium">Total Cost</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.tool_name} className="border-b border-gray-800/50">
                <td className="py-1.5 text-gray-300">{d.tool_name}</td>
                <td className="py-1.5 text-right">{d.call_count}</td>
                <td className="py-1.5 text-right">{d.total_input_tokens.toLocaleString()}</td>
                <td className="py-1.5 text-right">{d.total_output_tokens.toLocaleString()}</td>
                <td className="py-1.5 text-right text-yellow-400">${d.total_cost_usd.toFixed(6)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
