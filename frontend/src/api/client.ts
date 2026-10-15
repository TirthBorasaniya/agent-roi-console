import { createContext, useContext } from 'react'
import type {
  GatewayStats,
  GuardAction,
  Meta,
  OverviewItem,
  RoiView,
  RunDetail,
  RunOutcome,
  RunSummary,
  SensitivityView,
  WorkflowView,
} from './types'

// One interface, two implementations: HttpApi talks to the console API, StaticApi
// reads the JSON snapshot baked into the public demo build
export interface ConsoleApi {
  readonly o_read_only: boolean
  meta(): Promise<Meta>
  overview(): Promise<OverviewItem[]>
  workflows(): Promise<WorkflowView[]>
  workflow(id: string): Promise<WorkflowView>
  roi(id: string): Promise<RoiView>
  sensitivity(id: string): Promise<SensitivityView>
  runs(workflowId: string): Promise<RunSummary[]>
  run(runId: string): Promise<RunDetail>
  gatewayStats(): Promise<GatewayStats>
  guardLog(): Promise<GuardAction[]>
  triggerRun(workflowId: string): Promise<RunOutcome>
}

export const ApiContext = createContext<ConsoleApi | null>(null)

export function useApi(): ConsoleApi {
  const api = useContext(ApiContext)
  if (!api) {
    throw new Error('ApiContext is not provided')
  }
  return api
}
