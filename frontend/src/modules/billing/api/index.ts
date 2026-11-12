import { http } from '../../../shared/http.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'
import { parsePoints } from '../contracts.ts'
import type {
  CreditAccount,
  CreditAdjustment,
  CreditLedgerEntry,
  CreditPage,
  CreditRate,
  CreditRateCreate,
  CreditReconciliation,
  CreditState,
  CreditStateMutation,
  CreditSummary,
  CreditUsage,
  StateMutation
} from './types.ts'
export type * from './types.ts'

const root = '/billing'
const accountPath = (id?: string) =>
  id ? `${root}/accounts/${encodeURIComponent(id)}` : `${root}/me`
const nullableString = (value: unknown) => (value === null ? null : string(value))
export function parseCreditState(value: unknown): CreditState {
  const data = object(value)
  return { enabled: boolean(data.enabled), version: number(data.version) }
}
export function parseCreditSummary(value: unknown): CreditSummary {
  const data = object(value)
  return {
    ...parseCreditState(data),
    user_id: string(data.user_id),
    allocated_points: parsePoints(data.allocated_points),
    reclaimed_points: parsePoints(data.reclaimed_points),
    used_points: parsePoints(data.used_points),
    reserved_points: parsePoints(data.reserved_points),
    available_points: parsePoints(data.available_points)
  }
}
function parseAccount(value: unknown): CreditAccount {
  const data = object(value)
  return {
    ...parseCreditSummary(data),
    username: string(data.username),
    display_name: string(data.display_name),
    is_active: boolean(data.is_active)
  }
}
function parseLedger(value: unknown): CreditLedgerEntry {
  const data = object(value)
  return {
    id: string(data.id),
    user_id: string(data.user_id),
    operator_id: nullableString(data.operator_id),
    kind: string(data.kind),
    amount: parsePoints(data.amount),
    available_delta: parsePoints(data.available_delta),
    reserved_delta: parsePoints(data.reserved_delta),
    available_after: parsePoints(data.available_after),
    reserved_after: parsePoints(data.reserved_after),
    run_id: nullableString(data.run_id),
    reason: string(data.reason),
    created_at: string(data.created_at)
  }
}
export function parseCreditUsage(value: unknown): CreditUsage {
  const data = object(value)
  if (data.status !== 'reserved' && data.status !== 'unknown' && data.status !== 'settled')
    throw new Error('积分消费状态无效')
  if (data.billing_mode !== 'token' && data.billing_mode !== 'search_unit')
    throw new Error('积分计价方式无效')
  return {
    run_id: string(data.run_id),
    user_id: string(data.user_id),
    model_id: string(data.model_id),
    source: string(data.source),
    billing_mode: data.billing_mode,
    input_tokens: nullableNumber(data.input_tokens ?? null),
    output_tokens: nullableNumber(data.output_tokens ?? null),
    cache_tokens: nullableNumber(data.cache_tokens ?? null),
    search_units: nullableNumber(data.search_units ?? null),
    reasoning_tokens: nullableNumber(data.reasoning_tokens ?? null),
    status: data.status,
    version: number(data.version),
    reserved_points: parsePoints(data.reserved_points),
    charged_points: data.charged_points === null ? null : parsePoints(data.charged_points),
    failure_waived: data.failure_waived === undefined ? false : boolean(data.failure_waived),
    rate_version: number(data.rate_version),
    created_at: string(data.created_at)
  }
}
function parseRate(value: unknown): CreditRate {
  const data = object(value)
  if (data.billing_mode !== 'token' && data.billing_mode !== 'search_unit')
    throw new Error('积分计价方式无效')
  return {
    id: string(data.id),
    model_id: string(data.model_id),
    version: number(data.version),
    created_at: string(data.created_at),
    billing_mode: data.billing_mode,
    is_free: boolean(data.is_free),
    input_points: parsePoints(data.input_points),
    output_points: parsePoints(data.output_points),
    cache_points: parsePoints(data.cache_points),
    search_points: parsePoints(data.search_points)
  }
}
function page<T>(decode: (value: unknown) => T) {
  return (value: unknown): CreditPage<T> => {
    const data = object(value)
    return {
      items: list(decode)(data.items),
      total: number(data.total),
      page: number(data.page),
      page_size: number(data.page_size)
    }
  }
}
export const getCreditState = (signal: AbortSignal) =>
  http.request(`${root}/state`, { signal, decode: parseCreditState })
export const setCreditState = (body: CreditStateMutation, signal: AbortSignal) =>
  http.request(`${root}/state`, { method: 'PATCH', body, signal, decode: parseCreditState })
export const getMyCredits = (signal: AbortSignal) =>
  http.request(`${root}/me`, { signal, decode: parseCreditSummary })
export const getCreditAccounts = (search: string, pageNumber: number, signal: AbortSignal) =>
  http.request(
    `${root}/accounts?${new URLSearchParams({ search, page: String(pageNumber), page_size: '10' })}`,
    { signal, decode: page(parseAccount) }
  )
export const adjustCredits = (id: string, body: CreditAdjustment, signal: AbortSignal) =>
  http.request(`${accountPath(id)}/adjust`, {
    method: 'POST',
    body,
    signal,
    decode: parseCreditSummary
  })
export const setCreditAccountState = (id: string, body: StateMutation, signal: AbortSignal) =>
  http.request(`${accountPath(id)}/state`, {
    method: 'PATCH',
    body,
    signal,
    decode: parseCreditSummary
  })
export const getCreditLedger = (id: string | undefined, pageNumber: number, signal: AbortSignal) =>
  http.request(`${accountPath(id)}/ledger?page=${pageNumber}&page_size=10`, {
    signal,
    decode: page(parseLedger)
  })
export const getCreditUsage = (id: string | undefined, pageNumber: number, signal: AbortSignal) =>
  http.request(`${accountPath(id)}/usage?page=${pageNumber}&page_size=10`, {
    signal,
    decode: page(parseCreditUsage)
  })
export const reconcileCreditUsage = (
  runId: string,
  body: CreditReconciliation,
  signal: AbortSignal
) =>
  http.request(`${root}/usage/${encodeURIComponent(runId)}/reconcile`, {
    method: 'POST',
    body,
    signal,
    decode: parseCreditUsage
  })
export const getCreditRates = (modelId: string, signal: AbortSignal) =>
  http.request(`${root}/models/${encodeURIComponent(modelId)}/rates`, {
    signal,
    decode: list(parseRate)
  })
export const createCreditRate = (modelId: string, body: CreditRateCreate, signal: AbortSignal) =>
  http.request(`${root}/models/${encodeURIComponent(modelId)}/rates`, {
    method: 'POST',
    body,
    signal,
    decode: parseRate
  })
export const getCreditQuote = (modelId: string, signal: AbortSignal) =>
  http.request(`${root}/models/${encodeURIComponent(modelId)}/quote`, {
    signal,
    decode: (value) => (value === null ? null : parseRate(value))
  })
