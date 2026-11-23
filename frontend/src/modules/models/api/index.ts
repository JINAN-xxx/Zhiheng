import { http } from '../../../shared/http.ts'
import { parseRunSnapshot } from '../../../shared/stream.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'
export interface ConnectionOption {
  id: string
  name: string
  enabled: boolean
}
export interface ModelConnection {
  model_count?: number | undefined
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
  chat_input_budget?: {
    limit_tokens: number | null
    source: 'model_window' | 'platform' | 'model_window_and_platform' | 'unknown'
    reserved_output_tokens: number
    safety_margin_tokens: number
  } | null
  capacity_note?: string
  capacity_status?: 'unknown' | 'unverified' | 'documented' | 'not_applicable'
  availability?: {
    available: boolean
    reason:
      | 'model_disabled'
      | 'model_deleted'
      | 'connection_missing'
      | 'connection_disabled'
      | 'connection_deleted'
      | null
  } | null
  context_window_tokens?: number | null
  enabled: boolean
  model_type: 'chat' | 'embedding' | 'rerank'
  vision_enabled: boolean
  capabilities: ('chat' | 'stream' | 'embedding' | 'rerank' | 'vision')[]
}
function parseConnection(value: unknown): ModelConnection {
  const data = object(value)
  if (data.provider !== 'openai_compatible') throw new Error('不支持的模型连接类型')
  return {
    id: string(data.id),
    name: string(data.name),
    model_count: data.model_count == null ? undefined : number(data.model_count),
    provider: data.provider,
    base_url: string(data.base_url),
    has_api_key: boolean(data.has_api_key),
    enabled: boolean(data.enabled)
  }
}
function parseChatInputBudget(value: unknown): NonNullable<ChatModel['chat_input_budget']> {
  const data = object(value)
  if (
    !['model_window', 'platform', 'model_window_and_platform', 'unknown'].includes(
      string(data.source)
    )
  )
    throw new Error('模型输入预算来源无效')
  return {
    limit_tokens: data.limit_tokens === null ? null : number(data.limit_tokens),
    source: data.source as NonNullable<ChatModel['chat_input_budget']>['source'],
    reserved_output_tokens: number(data.reserved_output_tokens),
    safety_margin_tokens: number(data.safety_margin_tokens)
  }
}
function parseAvailability(value: unknown): NonNullable<ChatModel['availability']> {
  const data = object(value),
    reason = data.reason == null ? null : string(data.reason)
  if (
    reason !== null &&
    ![
      'model_disabled',
      'model_deleted',
      'connection_missing',
      'connection_disabled',
      'connection_deleted'
    ].includes(reason)
  )
    throw new Error('模型可用性原因无效')
  return {
    available: boolean(data.available),
    reason: reason as NonNullable<ChatModel['availability']>['reason']
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
  const capacityStatus = data.capacity_status
  if (
    capacityStatus !== undefined &&
    !['unknown', 'unverified', 'documented', 'not_applicable'].includes(string(capacityStatus))
  )
    throw new Error('模型容量依据状态无效')
  return {
    capacity_note: data.capacity_note == null ? '' : string(data.capacity_note),
    ...(capacityStatus === undefined
      ? {}
      : { capacity_status: capacityStatus as NonNullable<ChatModel['capacity_status']> }),
    availability: data.availability == null ? null : parseAvailability(data.availability),
    id: string(data.id),
    name: string(data.name),
    connection_id: string(data.connection_id),
    upstream_model: string(data.upstream_model),
    max_output_tokens: number(data.max_output_tokens),
    context_window_tokens:
      data.context_window_tokens == null ? null : number(data.context_window_tokens),
    chat_input_budget:
      data.chat_input_budget == null ? null : parseChatInputBudget(data.chat_input_budget),
    enabled: boolean(data.enabled),
    model_type: data.model_type,
    vision_enabled: data.vision_enabled === undefined ? false : boolean(data.vision_enabled),
    capabilities
  }
}
export const getModels = (signal: AbortSignal, includeDisabled = false) =>
  http.request(`/models${includeDisabled ? '?include_disabled=true' : ''}`, {
    signal,
    decode: list(parseModel)
  })
export const getTrialModels = (signal: AbortSignal) =>
  http.request('/models?trial=true', { signal, decode: list(parseModel) })
export interface ModelTrialPayload {
  prompt: string
  documents: string[]
}
export const startModelTrial = (
  id: string,
  body: ModelTrialPayload & { client_request_id: string },
  signal: AbortSignal
) =>
  http.request(`/models/${encodeURIComponent(id)}/trial`, {
    method: 'POST',
    body,
    signal,
    decode: parseRunSnapshot
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
    capacity_note?: string
    model_type?: 'chat' | 'embedding' | 'rerank'
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
    capacity_note?: string
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
  action: 'enable' | 'disable' | 'delete',
  signal: AbortSignal,
  retireKeyAccess = false
) => {
  const suffix = action === 'delete' ? '' : `/${action}`
  const query = action === 'delete' && retireKeyAccess ? '?retire_key_access=true' : ''
  return http.request(`/models/${encodeURIComponent(id)}${suffix}${query}`, {
    method: action === 'delete' ? 'DELETE' : 'POST',
    ...(action === 'delete' ? {} : { body: {} }),
    signal,
    decode: action === 'delete' ? () => undefined : parseModel
  })
}
export const getConnectionOptions = (signal: AbortSignal) =>
  http.request('/models/connection-options', {
    signal,
    decode: list((value) => {
      const row = object(value)
      return { id: string(row.id), name: string(row.name), enabled: boolean(row.enabled) }
    })
  })
