import { http } from './http.ts'
import { followRun, parseRunSnapshot } from './stream.ts'
import type { RunSnapshot } from './stream.ts'
export const getRun = (id: string, signal: AbortSignal) =>
  http.request(`/runs/${encodeURIComponent(id)}`, { signal, decode: parseRunSnapshot })
export const cancelRun = (id: string, signal: AbortSignal) =>
  http.request(`/runs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseRunSnapshot
  })
export const observeRun = (
  initial: RunSnapshot,
  update: (run: RunSnapshot) => void,
  signal: AbortSignal
) =>
  followRun(
    {
      get: (signal) => getRun(initial.id, signal),
      open: (signal) => http.open(`/runs/${encodeURIComponent(initial.id)}/events`, { signal })
    },
    initial,
    update,
    signal
  )
