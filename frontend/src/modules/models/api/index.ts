import { http } from '../../../shared/http.ts'
import { parseRunSnapshot } from '../../../shared/stream.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'
export interface ModelConnection {
  id: string
  name: string
  provider: 'openai_compatible'
  base_url: string
  has_api_key: boolean
  enabled: boolean
}
export interface ChatModel {
  id: string
  name: string
  connection_id: string
  upstream_model: string
  max_output_tokens: number
  context_window_tokens?: number | null
  enabled: boolean
  model_type: 'chat' | 'embedding' | 'rerank'
  published: boolean
  vision_enabled: boolean
  capabilities: ('chat' | 'stream' | 'embedding' | 'rerank' | 'vision')[]
}
function parseConnection(value: unknown): ModelConnection {
  const data = object(value)
  if (data.provider !== 'openai_compatible') throw new Error('不支持的模型连接类型')
  return {
    id: string(data.id),
    name: string(data.name),
    provider: data.provider,
    base_url: string(data.base_url),
    has_api_key: boolean(data.has_api_key),
    enabled: boolean(data.enabled)
  }
}
function parseModel(value: unknown): ChatModel {
  const data = object(value)
  const capabilities = list((value) => {
    if (
      value !== 'chat' &&
      value !== 'stream' &&
      value !== 'embedding' &&
      value !== 'rerank' &&
      value !== 'vision'
    )
      throw new Error('不支持的模型能力')
    return value
  })(data.capabilities)
  if (data.model_type !== 'chat' && data.model_type !== 'embedding' && data.model_type !== 'rerank')
    throw new Error('不支持的模型类型')
  return {
    id: string(data.id),
    name: string(data.name),
    connection_id: string(data.connection_id),
    upstream_model: string(data.upstream_model),
    max_output_tokens: number(data.max_output_tokens),
    context_window_tokens:
      data.context_window_tokens == null ? null : number(data.context_window_tokens),
    enabled: boolean(data.enabled),
    model_type: data.model_type,
    published: boolean(data.published),
    vision_enabled: data.vision_enabled === undefined ? false : boolean(data.vision_enabled),
    capabilities
  }
}
export const getModels = (signal: AbortSignal, includeDisabled = false) =>
  http.request(`/models${includeDisabled ? '?include_disabled=true' : ''}`, {
    signal,
    decode: list(parseModel)
  })
export const getConnections = (signal: AbortSignal) =>
  http.request('/models/connections', { signal, decode: list(parseConnection) })
export const createConnection = (
  body: { name: string; base_url: string; api_key: string },
  signal: AbortSignal
) => http.request('/models/connections', { method: 'POST', body, signal, decode: parseConnection })
export const createModel = (
  body: {
    name: string
    connection_id: string
    upstream_model: string
    max_output_tokens: number
    context_window_tokens?: number | null
    model_type?: 'chat' | 'embedding' | 'rerank'
    published?: boolean
    vision_enabled?: boolean
  },
  signal: AbortSignal
) => http.request('/models', { method: 'POST', body, signal, decode: parseModel })
export const updateConnection = (
  id: string,
  body: { name?: string; base_url?: string; api_key?: string; enabled?: boolean },
  signal: AbortSignal
) =>
  http.request(`/models/connections/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body,
    signal,
    decode: parseConnection
  })
export const controlConnection = (
  id: string,
  action: 'enable' | 'disable' | 'delete',
  signal: AbortSignal
) =>
  http.request(
    `/models/connections/${encodeURIComponent(id)}${action === 'delete' ? '' : `/${action}`}`,
    {
      method: action === 'delete' ? 'DELETE' : 'POST',
      ...(action === 'delete' ? {} : { body: {} }),
      signal,
      decode: action === 'delete' ? () => undefined : parseConnection
    }
  )
export const updateModel = (
  id: string,
  body: {
    name: string
    connection_id: string
    upstream_model: string
    max_output_tokens: number
    context_window_tokens?: number | null
    vision_enabled?: boolean
  },
  signal: AbortSignal
) =>
  http.request(`/models/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body,
    signal,
    decode: parseModel
  })
export const controlModel = (
  id: string,
  action: 'enable' | 'disable' | 'publish' | 'unpublish' | 'delete',
  signal: AbortSignal
) =>
  http.request(`/models/${encodeURIComponent(id)}${action === 'delete' ? '' : `/${action}`}`, {
    method: action === 'delete' ? 'DELETE' : 'POST',
    ...(action === 'delete' ? {} : { body: {} }),
    signal,
    decode: action === 'delete' ? () => undefined : parseModel
  })
export interface RateInput {
  billing_mode: 'token' | 'search_unit'
  input_price: string
  output_price: string
  cache_price: string
  search_price: string
  currency: string
}
export interface RateCard extends RateInput {
  id: string
  model_id: string
  version: number
  created_at: string
}
function parseRate(value: unknown): RateCard {
  const data = object(value)
  if (data.billing_mode !== 'token' && data.billing_mode !== 'search_unit')
    throw new Error('费率计费方式无效')
  return {
    id: string(data.id),
    model_id: string(data.model_id),
    version: number(data.version),
    created_at: string(data.created_at),
    billing_mode: data.billing_mode,
    input_price: string(data.input_price),
    output_price: string(data.output_price),
    cache_price: string(data.cache_price),
    search_price: string(data.search_price),
    currency: string(data.currency)
  }
}
export const getRateCards = (id: string, signal: AbortSignal) =>
  http.request(`/models/${encodeURIComponent(id)}/rate-cards`, { signal, decode: list(parseRate) })
export const createRateCard = (id: string, body: RateInput, signal: AbortSignal) =>
  http.request(`/models/${encodeURIComponent(id)}/rate-cards`, {
    method: 'POST',
    body,
    signal,
    decode: parseRate
  })
export const startModelTest = (id: string, client_request_id: string, signal: AbortSignal) =>
  http.request(`/models/${encodeURIComponent(id)}/test`, {
    method: 'POST',
    body: { client_request_id },
    signal,
    decode: parseRunSnapshot
  })
export {
  cancelRun as cancelModelTest,
  observeRun as observeModelTest
} from '../../../shared/runs.ts'
