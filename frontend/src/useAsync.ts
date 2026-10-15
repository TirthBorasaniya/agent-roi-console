import { useEffect, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

// load once per dependency change; stale responses are ignored
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({ data: null, error: null, loading: true })
  useEffect(() => {
    let o_active = true
    setState({ data: null, error: null, loading: true })
    load()
      .then((data) => o_active && setState({ data, error: null, loading: false }))
      .catch((e: Error) => o_active && setState({ data: null, error: e.message, loading: false }))
    return () => {
      o_active = false
    }
  }, deps)
  return state
}
