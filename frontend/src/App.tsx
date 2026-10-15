import { useMemo } from 'react'
import { HashRouter, Route, Routes } from 'react-router-dom'
import { ApiContext } from './api/client'
import type { ConsoleApi } from './api/client'
import { createHttpApi } from './api/http'
import { createStaticApi } from './api/static'
import { Layout } from './components/Layout'
import Gateway from './pages/Gateway'
import GuardLog from './pages/GuardLog'
import Overview from './pages/Overview'
import WorkflowDetail from './pages/WorkflowDetail'

// hash routing so the static demo works on GitHub Pages without server rewrites
export function AppRoutes() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/workflows/:workflowId" element={<WorkflowDetail />} />
        <Route path="/gateway" element={<Gateway />} />
        <Route path="/guard" element={<GuardLog />} />
      </Routes>
    </Layout>
  )
}

export default function App({ api }: { api?: ConsoleApi }) {
  const client = useMemo(
    () => api ?? (import.meta.env.VITE_DATA_SOURCE === 'static' ? createStaticApi() : createHttpApi()),
    [api],
  )
  return (
    <ApiContext.Provider value={client}>
      <HashRouter>
        <AppRoutes />
      </HashRouter>
    </ApiContext.Provider>
  )
}
