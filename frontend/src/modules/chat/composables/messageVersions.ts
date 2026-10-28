import type { ChatMessage } from '../api/index.ts'

export interface MessageVersions {
  key: string
  questionId: string | null
  versions: ChatMessage[]
}
export function groupMessageVersions(messages: readonly ChatMessage[]): MessageVersions[] {
  const rows: MessageVersions[] = []
  const replies = new Map<string, MessageVersions>()
  let previousQuestion: string | null = null
  for (const message of messages) {
    if (message.role === 'user') {
      previousQuestion = message.id
      rows.push({ key: message.id, questionId: message.id, versions: [message] })
      continue
    }
    const questionId = message.reply_to_message_id ?? previousQuestion
    const key = questionId ? `reply:${questionId}` : message.id
    const existing = replies.get(key)
    if (existing) existing.versions.push(message)
    else {
      const row = { key, questionId, versions: [message] }
      replies.set(key, row)
      rows.push(row)
    }
  }
  for (const row of rows) row.versions.sort((a, b) => a.generation_index - b.generation_index)
  return rows
}
export interface RegenerationIntent {
  conversationId: string
  messageId: string
  questionId: string | null
  generationIndex: number
  clientRequestId: string
}
export function nextRegenerationIntent(
  pending: RegenerationIntent | null,
  conversationId: string,
  message: ChatMessage,
  createId: () => string
): RegenerationIntent {
  if (pending) {
    if (pending.conversationId !== conversationId || pending.messageId !== message.id)
      throw new Error('重新生成结果待确认，请使用原请求重试')
    return pending
  }
  return {
    conversationId,
    messageId: message.id,
    questionId: message.reply_to_message_id,
    generationIndex: message.generation_index,
    clientRequestId: createId()
  }
}
