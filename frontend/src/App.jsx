import { useState } from 'react'
import MetricsSummary from './components/MetricsSummary'
import ROILedger from './components/ROILedger'
import CostAttribution from './components/CostAttribution'
import WorkflowList from './components/WorkflowList'
import RunHistory from './components/RunHistory'

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'workflows', label: 'Workflows' },
  { id: 'runs', label: 'Run History' },
  { id: 'costs', label: 'Cost Attribution' },
]

function Section({ title, children }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-5">{title}</h2>
      {children}
    </div>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard')
  const [refreshKey, setRefreshKey] = useState(0)

  const onRunComplete = () => setRefreshKey((k) => k + 1)

  return (
    <div className="min-h-screen flex bg-gray-950">
      {/* Sidebar */}
      <aside className="w-52 flex-shrink-0 border-r border-gray-800 flex flex-col py-6 px-4 gap-1">
        <div className="mb-6">
          <h1 className="text-white font-semibold text-sm">Agent ROI Console</h1>
          <p className="text-gray-500 text-xs mt-0.5">Automation value tracking</p>
        </div>
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            onClick={() => setActiveTab(item.id)}
            className={`text-left px-3 py-2 rounded-lg text-sm transition-colors ${
              activeTab === item.id
                ? 'bg-blue-600 text-white font-medium'
                : 'text-gray-400 hover:text-white hover:bg-gray-800'
            }`}
          >
            {item.label}
          </button>
        ))}
      </aside>

      {/* Main content */}
      <main className="flex-1 p-8 overflow-y-auto">
        {activeTab === 'dashboard' && (
          <div className="flex flex-col gap-6">
            <MetricsSummary key={`metrics-${refreshKey}`} />
            <Section title="ROI Ledger">
              <ROILedger key={`ledger-${refreshKey}`} />
            </Section>
          </div>
        )}

        {activeTab === 'workflows' && (
          <Section title="Workflows">
            <WorkflowList onRunComplete={onRunComplete} key={`wf-${refreshKey}`} />
          </Section>
        )}

        {activeTab === 'runs' && (
          <Section title="Run History">
            <RunHistory key={`runs-${refreshKey}`} />
          </Section>
        )}

        {activeTab === 'costs' && (
          <Section title="Cost Attribution by Tool">
            <CostAttribution key={`costs-${refreshKey}`} />
          </Section>
        )}
      </main>
    </div>
  )
}
