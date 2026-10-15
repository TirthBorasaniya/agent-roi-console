import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useApi } from '../api/client'
import { useAsync } from '../useAsync'

const NAV_ITEMS = [
  { to: '/', label: 'Overview' },
  { to: '/gateway', label: 'Gateway' },
  { to: '/guard', label: 'Guard log' },
]

export function DemoBanner() {
  const api = useApi()
  const { data } = useAsync(() => api.meta(), [api])
  if (!data || !(data.o_demo || data.o_static)) return null
  const when = data.snapshot_date ? ` from evaluation runs on ${data.snapshot_date}` : ''
  return (
    <div role="status" className="bg-amber-900/60 border-b border-amber-700 text-amber-100 text-sm px-6 py-2">
      Demo data{when}. {data.o_static ? 'This public page is a read-only snapshot.' : 'Connectors are on recorded fixtures.'}
    </div>
  )
}

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col bg-gray-950">
      <DemoBanner />
      <div className="flex flex-1">
        <aside className="w-52 flex-shrink-0 border-r border-gray-800 flex flex-col py-6 px-4 gap-1">
          <div className="mb-6">
            <h1 className="text-white font-semibold text-sm">Agent ROI Console</h1>
            <p className="text-gray-500 text-xs mt-0.5">Measured automation ROI</p>
          </div>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `px-3 py-2 rounded-lg text-sm ${isActive ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800'}`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </aside>
        <main className="flex-1 p-8 overflow-y-auto">{children}</main>
      </div>
    </div>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider mb-4">{title}</h2>
      {children}
    </section>
  )
}

export function StatCard({ label, value, sub }: { label: string; value: ReactNode; sub?: string }) {
  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800 flex flex-col gap-1">
      <span className="text-xs text-gray-400 uppercase tracking-wider">{label}</span>
      <span className="text-2xl font-semibold text-white">{value}</span>
      {sub && <span className="text-xs text-gray-500">{sub}</span>}
    </div>
  )
}

export function Loading({ error, what }: { error: string | null; what: string }) {
  if (error) return <p className="text-red-400 text-sm">Failed to load {what}: {error}</p>
  return <p className="text-gray-500 text-sm">Loading {what}...</p>
}
