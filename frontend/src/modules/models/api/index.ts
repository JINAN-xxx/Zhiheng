import { http } from '../../../shared/http.ts'
import { followRun, parseRunSnapshot } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'
export interface ModelConnection {
  id: string
  name: string
  provider: 'openai_compatible'
  base_url: string
  has_api_key: boolean
}
export interface ChatModel {
  id: string
  name: string
  connection_id: string
  upstream_model: string
  max_output_tokens: number
  enabled: boolean
  capabilities: ('chat' | 'stream')[]
}
function parseConnection(value: unknown): ModelConnection {
  const data = object(value)
  if (data.provider !== 'openai_compatible') throw new Error('不支持的模型连接类型')
  return {
    id: string(data.id),
    name: string(data.name),
    provider: data.provider,
    base_url: string(data.base_url),
    has_api_key: boolean(data.has_api_key)
  }
}
function parseModel(value: unknown): ChatModel {
  const data = object(value)
  const capabilities = list((value) => {
    if (value !== 'chat' && value !== 'stream') throw new Error('不支持的模型能力')
    return value
  })(data.capabilities)
  return {
    id: string(data.id),
    name: string(data.name),
    connection_id: string(data.connection_id),
    upstream_model: string(data.upstream_model),
    max_output_tokens: number(data.max_output_tokens),
    enabled: boolean(data.enabled),
    capabilities
  }
}
export const getModels = (signal: AbortSignal) =>
  http.request('/models', { signal, decode: list(parseModel) })
export const getConnections = (signal: AbortSignal) =>
  http.request('/models/connections', { signal, decode: list(parseConnection) })
export const createConnection = (
  body: { name: string; base_url: string; api_key: string },
  signal: AbortSignal
) => http.request('/models/connections', { method: 'POST', body, signal, decode: parseConnection })
export const createModel = (
  body: { name: string; connection_id: string; upstream_model: string; max_output_tokens: number },
  signal: AbortSignal
) => http.request('/models', { method: 'POST', body, signal, decode: parseModel })
export const startModelTest = (id: string, client_request_id: string, signal: AbortSignal) =>
  http.request(`/models/${encodeURIComponent(id)}/test`, {
    method: 'POST',
    body: { client_request_id },
    signal,
    decode: parseRunSnapshot
  })
export const cancelModelTest = (id: string, signal: AbortSignal) =>
  http.request(`/runs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseRunSnapshot
  })
export const observeModelTest = (
  initial: RunSnapshot,
  update: (run: RunSnapshot) => void,
  signal: AbortSignal
) =>
  followRun(
    {
      get: (signal) =>
        http.request(`/runs/${encodeURIComponent(initial.id)}`, {
          signal,
          decode: parseRunSnapshot
        }),
      open: (signal) => http.open(`/runs/${encodeURIComponent(initial.id)}/events`, { signal })
    },
    initial,
    update,
    signal
  )
