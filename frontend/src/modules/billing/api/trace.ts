import { http } from '../../../shared/http.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'
import { parseCreditUsage, parseLedger } from './index.ts'
import type { CreditLedgerEntry, CreditReconciliation, CreditUsage } from './types.ts'

export interface InvocationReference {
  run_id: string
  source: string
  status: string
  created_at: string
}
export interface InvocationDetail extends InvocationReference {
  id: string
  version: number
  user_id: string
  model_id: string
  operation: string
  application_id: string | null
  key_id: string | null
  points_enforced: boolean
  reserved_tokens: number
  input_tokens: number | null
  output_tokens: number | null
  charged_tokens: number | null
  cache_tokens: number | null
  reasoning_tokens: number | null
  search_units: number | null
  currency: string | null
  reserved_cost: string | null
  billable_cost: string | null
  rate_card_id: string | null
  rate_version: number | null
}
export interface InvocationTrace {
  billing: {
    invocation: InvocationDetail
    reservation: CreditUsage | null
    ledger: CreditLedgerEntry[]
    ledger_total: number
    ledger_page: number
    ledger_page_size: number
  }
  execution: {
    id: string
    owner_id: string
    billing_owner_id: string
    resource_type: string
    status: string
    attempt_no: number
    created_at: string
  } | null
  related: (InvocationReference & { relation: 'parent' | 'summary' })[]
  related_has_more: boolean
  observed_at: string
}
const nullableString = (value: unknown) => (value === null ? null : string(value))
function currencyAmount(value: unknown): string | null {
  if (value === null) return null
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)(\.\d{1,12})?$/.test(value))
    throw new Error('服务器返回的货币金额须为精确十进制字符串')
  return value
}
function reference(value: unknown): InvocationReference {
  const data = object(value)
  return {
    run_id: string(data.run_id),
    source: string(data.source),
    status: string(data.status),
    created_at: string(data.created_at)
  }
}
export function parseInvocationTrace(value: unknown): InvocationTrace {
  const data = object(value),
    billing = object(data.billing),
    row = object(billing.invocation)
  const execution = data.execution === null ? null : object(data.execution)
  return {
    billing: {
      invocation: {
        ...reference(row),
        id: string(row.id),
        version: number(row.version),
        user_id: string(row.user_id),
        model_id: string(row.model_id),
        operation: string(row.operation),
        application_id: nullableString(row.application_id),
        key_id: nullableString(row.key_id),
        points_enforced: boolean(row.points_enforced),
        reserved_tokens: number(row.reserved_tokens),
        input_tokens: nullableNumber(row.input_tokens),
        output_tokens: nullableNumber(row.output_tokens),
        charged_tokens: nullableNumber(row.charged_tokens),
        cache_tokens: nullableNumber(row.cache_tokens),
        reasoning_tokens: nullableNumber(row.reasoning_tokens),
        search_units: nullableNumber(row.search_units),
        currency: nullableString(row.currency),
        reserved_cost: currencyAmount(row.reserved_cost),
        billable_cost: currencyAmount(row.billable_cost),
        rate_card_id: nullableString(row.rate_card_id),
        rate_version: nullableNumber(row.rate_version)
      },
      reservation: billing.reservation === null ? null : parseCreditUsage(billing.reservation),
      ledger: list(parseLedger)(billing.ledger),
      ledger_total: number(billing.ledger_total),
      ledger_page: number(billing.ledger_page),
      ledger_page_size: number(billing.ledger_page_size)
    },
    execution:
      execution === null
        ? null
        : {
            id: string(execution.id),
            owner_id: string(execution.owner_id),
            billing_owner_id: string(execution.billing_owner_id),
            resource_type: string(execution.resource_type),
            status: string(execution.status),
            attempt_no: number(execution.attempt_no),
            created_at: string(execution.created_at)
          },
    related: list((value): InvocationTrace['related'][number] => {
      const item = object(value)
      if (item.relation !== 'parent' && item.relation !== 'summary')
        throw new Error('调用关联类型无效')
      return { ...reference(item), relation: item.relation }
    })(data.related),
    related_has_more: boolean(data.related_has_more),
    observed_at: string(data.observed_at)
  }
}
export const getInvocationTrace = (runId: string, page: number, signal: AbortSignal) =>
  http.request(`/billing/usage/${encodeURIComponent(runId)}?ledger_page=${page}`, {
    signal,
    decode: parseInvocationTrace
  })
export const reconcileInvocationTokens = (
  runId: string,
  body: CreditReconciliation,
  signal: AbortSignal
) =>
  http.request(`/usage/${encodeURIComponent(runId)}/reconcile`, {
    method: 'POST',
    body,
    signal,
    decode: object
  })
