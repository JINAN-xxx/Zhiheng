import type { ChatContextInfo } from '../../src/modules/chat/contextInfo.ts'

export const fullChatContext: ChatContextInfo = {
  strategy: 'token',
  status: 'full',
  effective_memory: {
    summary_used: false,
    covered_rounds: 0,
    retained_rounds: 0,
    estimated_tokens: 0
  },
  last_run_context: null,
  reason: null
}
