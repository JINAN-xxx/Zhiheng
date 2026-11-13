import { http } from '../../../shared/http.ts'
import { list, nullableNumber, number, object, string } from '../../../shared/validation.ts'
export interface UsageRecord {
  version: number
  points_enforced: boolean
  id: string
  run_id: string
  model_id: string
  source: string
  status: string
  input_tokens: number | null
  output_tokens: number | null
  charged_tokens: number | null
  created_at: string
}
export interface UsageSummary {
  quota_version: number
  points_enabled: boolean
  total: number
  page: number
  page_size: number
  limit_tokens: number
  used_tokens: number
  reserved_tokens: number
  records: UsageRecord[]
}
function parseRecord(value: unknown): UsageRecord {
  const data = object(value)
  return {
    version: data.version === undefined ? 1 : number(data.version),
    points_enforced: data.points_enforced === true,
    id: string(data.id),
    run_id: string(data.run_id),
    model_id: string(data.model_id),
    source: string(data.source),
    status: string(data.status),
    input_tokens: nullableNumber(data.input_tokens),
    output_tokens: nullableNumber(data.output_tokens),
    charged_tokens: nullableNumber(data.charged_tokens),
    created_at: string(data.created_at)
  }
}
export interface UsageQuery {
  page?: number
  pageSize?: number
  source?: string
  status?: string
  userId?: string | undefined
}
function parseSummary(value: unknown): UsageSummary {
  const data = object(value)
  return {
    limit_tokens: number(data.limit_tokens),
    used_tokens: number(data.used_tokens),
    reserved_tokens: number(data.reserved_tokens),
    quota_version: data.quota_version === undefined ? 0 : number(data.quota_version),
    points_enabled: data.points_enabled === true,
    total: data.total === undefined ? list(parseRecord)(data.records).length : number(data.total),
    page: data.page === undefined ? 1 : number(data.page),
    page_size: data.page_size === undefined ? 100 : number(data.page_size),
    records: list(parseRecord)(data.records)
  }
}
export const getUsage = (signal: AbortSignal, query: UsageQuery = {}) => {
  const params = new URLSearchParams({
    page: String(query.page ?? 1),
    page_size: String(query.pageSize ?? 20)
  })
  if (query.source) params.set('source', query.source)
  if (query.status) params.set('status', query.status)
  if (query.userId) params.set('user_id', query.userId)
  return http.request(`/usage?${params}`, { signal, decode: parseSummary })
}
export interface TokenReconciliation {
  expected_version: number
  reason: string
  client_request_id: string
  rejected_before_execution: boolean
  input_tokens: number | null
  output_tokens: number | null
  cache_tokens: number | null
  search_units: number | null
}
export const reconcileTokenUsage = (
  runId: string,
  body: TokenReconciliation,
  signal: AbortSignal
) =>
  http.request(`/usage/${encodeURIComponent(runId)}/reconcile`, {
    method: 'POST',
    body,
    signal,
    decode: parseRecord
  })
