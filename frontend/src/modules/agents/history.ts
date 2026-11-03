import type { PreviewMessage } from './api/index.ts'

export interface PreviewHistoryMessage extends PreviewMessage {
  status?: string
}

export function selectPreviewMessages(
  messages: readonly PreviewHistoryMessage[],
  historyRounds: number
): PreviewMessage[] {
  const current = messages.at(-1)
  if (!current) return []
  const rounds: PreviewMessage[][] = []
  let group: PreviewMessage[] = []
  for (const message of messages.slice(0, -1)) {
    if (message.role === 'user') {
      if (group.some((item) => item.role === 'assistant')) rounds.push(group)
      group = []
    }
    if (
      message.content.trim() &&
      (message.role === 'user' || message.status === undefined || message.status === 'completed')
    )
      group.push({ role: message.role, content: message.content })
  }
  if (group.some((item) => item.role === 'assistant')) rounds.push(group)
  return [
    ...(historyRounds ? rounds.slice(-historyRounds).flat() : []),
    { role: current.role, content: current.content }
  ]
}
