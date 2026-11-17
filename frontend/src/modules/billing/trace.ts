import type { InvocationTrace } from './api/trace.ts'

const counters = [
  'input_tokens',
  'output_tokens',
  'cache_tokens',
  'search_units',
  'reasoning_tokens'
] as const
export interface ReconciliationTarget {
  kind: 'credits' | 'tokens'
  run_id: string
  version: number
  billing_mode: 'token' | 'search_unit'
  input_tokens: number | null
  output_tokens: number | null
  cache_tokens: number | null
  search_units: number | null
  reasoning_tokens: number | null
  requires_search_units: boolean
}
export function reconciliationTarget(trace: InvocationTrace): ReconciliationTarget | null {
  const row = trace.billing.invocation,
    hold = trace.billing.reservation
  if (row.points_enforced ? hold?.status !== 'unknown' : row.status !== 'unknown') return null
  const target: ReconciliationTarget = {
    kind: row.points_enforced ? 'credits' : 'tokens',
    run_id: row.run_id,
    version: row.points_enforced ? hold!.version : row.version,
    billing_mode: row.points_enforced ? hold!.billing_mode : 'token',
    input_tokens: row.input_tokens,
    output_tokens: row.output_tokens,
    cache_tokens: row.cache_tokens,
    search_units: row.search_units,
    reasoning_tokens: row.reasoning_tokens,
    requires_search_units:
      !row.points_enforced && row.operation === 'rerank' && row.rate_card_id !== null
  }
  if (row.points_enforced && hold) {
    for (const counter of counters) {
      if (row[counter] !== null && hold[counter] !== null && row[counter] !== hold[counter])
        return null
      target[counter] = row[counter] ?? hold[counter]
    }
  }
  return target
}
export function acceptedAge(createdAt: string, observedAt = new Date().toISOString()): string {
  const seconds = Math.floor((Date.parse(observedAt) - Date.parse(createdAt)) / 1000)
  if (!Number.isFinite(seconds) || seconds < 0) return '时间待确认'
  if (seconds < 60) return '不足 1 分钟'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天`
}
export const tokenStatus = (status: string): string =>
  ({ pending: '待核算', known: '已核算', unknown: '待核对' })[status] ?? status
export const executionStatus = (status: string): string =>
  ({
    pending: '排队中',
    running: '执行中',
    cancel_requested: '取消中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '已中断'
  })[status] ?? status
