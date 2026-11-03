import { boolean, nullableNumber, number, object, string } from '../../shared/validation.ts'

export interface ChatContextInfo {
  status: 'full' | 'summarizing' | 'summarized' | 'degraded'
  summary_version: number
  covered_rounds: number
  retained_rounds: number
  dirty: boolean
  truncated: boolean
  dropped_rounds: number
  estimated_input_tokens: number | null
  input_budget: number
  reason: string | null
}
export function parseContextInfo(value: unknown): ChatContextInfo {
  const data = object(value)
  if (!['full', 'summarizing', 'summarized', 'degraded'].includes(String(data.status)))
    throw new Error('会话上下文状态格式不正确')
  return {
    status: data.status as ChatContextInfo['status'],
    summary_version: number(data.summary_version),
    covered_rounds: number(data.covered_rounds),
    retained_rounds: number(data.retained_rounds),
    dirty: boolean(data.dirty),
    truncated: boolean(data.truncated),
    dropped_rounds: number(data.dropped_rounds),
    estimated_input_tokens: nullableNumber(data.estimated_input_tokens),
    input_budget: number(data.input_budget),
    reason: data.reason == null ? null : string(data.reason)
  }
}
export function contextDescription(info: ChatContextInfo): string {
  const dropped = info.truncated ? '部分较早对话未带入本次回答。' : ''
  if (info.status === 'degraded') return `较早对话暂未完成整理，可继续聊天。${dropped}`
  if (info.status === 'summarizing') return `正在整理会话摘要，可继续聊天。${dropped}`
  if (dropped) return dropped
  return info.status === 'summarized' ? '已整理较早对话' : ''
}
export interface ContextRetryIntent {
  ownerId: string
  conversationId: string
  requestId: string
}
export function nextContextRetryIntent(
  previous: ContextRetryIntent | null,
  ownerId: string,
  conversationId: string,
  createId: () => string = () => crypto.randomUUID()
): ContextRetryIntent {
  if (previous?.ownerId === ownerId && previous.conversationId === conversationId) return previous
  return { ownerId, conversationId, requestId: createId() }
}
