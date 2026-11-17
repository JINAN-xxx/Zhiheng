import { boolean, nullableNumber, number, object, string } from '../../shared/validation.ts'

export interface ChatEffectiveMemory {
  summary_used: boolean
  covered_rounds: number
  retained_rounds: number
  estimated_tokens: number | null
}
export interface ChatLastRunContext {
  run_id: string
  summary_prepared: boolean
  estimated_input_tokens: number | null
  input_budget: number | null
  summary_trigger_tokens: number
  retained_token_budget: number
}
export interface ChatContextInfo {
  status: 'full' | 'summarizing' | 'summarized' | 'degraded'
  strategy: 'token'
  effective_memory: ChatEffectiveMemory
  last_run_context: ChatLastRunContext | null
  reason: string | null
}
export function parseContextInfo(value: unknown): ChatContextInfo {
  const data = object(value),
    status = string(data.status),
    memory = object(data.effective_memory)
  if (!['full', 'summarizing', 'summarized', 'degraded'].includes(status))
    throw new Error('会话上下文状态格式不正确')
  if (data.strategy !== 'token') throw new Error('会话上下文策略格式不正确')
  const run = data.last_run_context === null ? null : object(data.last_run_context)
  return {
    status: status as ChatContextInfo['status'],
    strategy: 'token',
    effective_memory: {
      summary_used: boolean(memory.summary_used),
      covered_rounds: number(memory.covered_rounds),
      retained_rounds: number(memory.retained_rounds),
      estimated_tokens: nullableNumber(memory.estimated_tokens)
    },
    last_run_context:
      run === null
        ? null
        : {
            run_id: string(run.run_id),
            summary_prepared: boolean(run.summary_prepared),
            estimated_input_tokens: nullableNumber(run.estimated_input_tokens),
            input_budget: nullableNumber(run.input_budget),
            summary_trigger_tokens: number(run.summary_trigger_tokens),
            retained_token_budget: number(run.retained_token_budget)
          },
    reason: data.reason == null ? null : string(data.reason)
  }
}
export function contextDescription(info: ChatContextInfo): string {
  if (info.status === 'degraded') {
    if (info.reason === 'chat_checkpoint_missing') return '会话有效记忆已不可用，请新建对话后继续。'
    return '会话摘要暂未按 Token 预算整理完成，已保留原有效记忆；请重新发送问题或重新生成回复。'
  }
  if (info.status === 'summarizing') return '正在按 Token 预算整理会话摘要。'
  if (info.status === 'summarized') return '已按 Token 预算整理较早对话'
  return ''
}
