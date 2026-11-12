import type { CreditReconciliation, CreditUsage } from './api/types.ts'

type UsageCounts = Pick<
  CreditUsage,
  'input_tokens' | 'output_tokens' | 'cache_tokens' | 'search_units'
>
type ReconciliationSnapshot = UsageCounts &
  Pick<CreditUsage, 'version' | 'billing_mode'> &
  Partial<Pick<CreditUsage, 'reasoning_tokens'>>
export type ReconciliationDraft = UsageCounts &
  Pick<CreditReconciliation, 'reason' | 'rejected_before_execution'>

export function reconciliationDraft(row: UsageCounts): ReconciliationDraft {
  return {
    input_tokens: row.input_tokens,
    output_tokens: row.output_tokens,
    cache_tokens: row.cache_tokens,
    search_units: row.search_units,
    reason: '',
    rejected_before_execution: false
  }
}
export function hasConfirmedUsage(
  row: UsageCounts & Partial<Pick<CreditUsage, 'reasoning_tokens'>>
): boolean {
  return [
    row.input_tokens,
    row.output_tokens,
    row.cache_tokens,
    row.search_units,
    row.reasoning_tokens
  ].some((value) => value !== null && value !== undefined && value > 0)
}
export function reconciliationPayload(
  row: ReconciliationSnapshot,
  draft: ReconciliationDraft
): Omit<CreditReconciliation, 'client_request_id'> {
  const reason = draft.reason.trim()
  if (!reason) throw new Error('请填写核对依据')
  if (draft.rejected_before_execution) {
    if (hasConfirmedUsage(row)) throw new Error('已有实际用量，不能标记为执行前拒绝')
    return {
      expected_version: row.version,
      reason,
      rejected_before_execution: true,
      input_tokens: 0,
      output_tokens: 0,
      cache_tokens: 0,
      search_units: 0
    }
  }
  const counts: UsageCounts = {
    input_tokens: row.input_tokens ?? draft.input_tokens,
    output_tokens: row.output_tokens ?? draft.output_tokens,
    cache_tokens: row.cache_tokens ?? draft.cache_tokens,
    search_units: row.search_units ?? draft.search_units
  }
  if (
    Object.values(counts).some(
      (value) =>
        value !== null && (!Number.isSafeInteger(value) || value < 0 || value > 2_147_483_647)
    )
  )
    throw new Error('实际用量须为 0 至 2,147,483,647 的整数；未知值请留空')
  if (row.billing_mode === 'search_unit') {
    if (counts.search_units === null) throw new Error('请确认实际检索单元；未知值不能按零提交')
  } else if (counts.input_tokens === null || counts.output_tokens === null) {
    throw new Error('请确认实际输入与输出 Token；未知值不能按零提交')
  }
  if (
    counts.input_tokens !== null &&
    counts.cache_tokens !== null &&
    counts.cache_tokens > counts.input_tokens
  )
    throw new Error('缓存 Token 不可超过输入 Token')
  return { ...counts, reason, expected_version: row.version, rejected_before_execution: false }
}
export const formatUsageCount = (value: number | null): string =>
  value === null ? '未提供' : value.toLocaleString('zh-CN')
