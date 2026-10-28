import { decodeVoid, http } from '../../../shared/http.ts'
import { list, object, string } from '../../../shared/validation.ts'
import { parseRunAdmission } from '../../knowledge/public.ts'

export type FeedbackValue = 'up' | 'down' | null
export interface MessageFeedback {
  message_id: string
  value: FeedbackValue
}
export interface SharePreview {
  message_id: string
  question: string | null
  answer: string
  content_hash: string
}
export interface MessageShare {
  id: string
  message_id: string
  created_at: string
  expires_at: string
  revoked_at: string | null
}
export interface CreatedMessageShare extends MessageShare {
  token: string
}
export interface SharedMessage {
  question: string | null
  answer: string
  created_at: string
  expires_at: string
}
export function parseFeedback(value: unknown): MessageFeedback {
  const data = object(value)
  if (data.value !== null && data.value !== 'up' && data.value !== 'down')
    throw new Error('消息反馈格式不正确')
  return { message_id: string(data.message_id), value: data.value }
}
export function parseSharePreview(value: unknown): SharePreview {
  const data = object(value)
  return {
    message_id: string(data.message_id),
    question: data.question === null ? null : string(data.question),
    answer: string(data.answer),
    content_hash: string(data.content_hash)
  }
}
export function parseMessageShare(value: unknown): MessageShare {
  const data = object(value)
  return {
    id: string(data.id),
    message_id: string(data.message_id),
    created_at: string(data.created_at),
    expires_at: string(data.expires_at),
    revoked_at: data.revoked_at === null ? null : string(data.revoked_at)
  }
}
function parseCreatedShare(value: unknown): CreatedMessageShare {
  return { ...parseMessageShare(value), token: string(object(value).token) }
}
export function parseSharedMessage(value: unknown): SharedMessage {
  const data = object(value)
  return {
    question: data.question === null ? null : string(data.question),
    answer: string(data.answer),
    created_at: string(data.created_at),
    expires_at: string(data.expires_at)
  }
}
export const regenerateMessage = (
  conversationId: string,
  messageId: string,
  clientRequestId: string,
  signal: AbortSignal
) =>
  http.request(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/regenerate`,
    {
      method: 'POST',
      body: { client_request_id: clientRequestId },
      signal,
      decode: parseRunAdmission
    }
  )
export const setMessageFeedback = (messageId: string, value: FeedbackValue, signal: AbortSignal) =>
  http.request(`/chat/messages/${encodeURIComponent(messageId)}/feedback`, {
    method: 'PUT',
    body: { value },
    signal,
    decode: parseFeedback
  })
export const getSharePreview = (messageId: string, includeQuestion: boolean, signal: AbortSignal) =>
  http.request(
    `/chat/messages/${encodeURIComponent(messageId)}/share-preview?include_question=${includeQuestion}`,
    {
      signal,
      decode: parseSharePreview
    }
  )
export const createMessageShare = (
  messageId: string,
  body: { include_question: boolean; expires_in_days: 1 | 7 | 30; expected_content_hash: string },
  signal: AbortSignal
) =>
  http.request(`/chat/messages/${encodeURIComponent(messageId)}/shares`, {
    method: 'POST',
    body,
    signal,
    decode: parseCreatedShare
  })
export const getMessageShares = (messageId: string, signal: AbortSignal) =>
  http.request(`/chat/messages/${encodeURIComponent(messageId)}/shares`, {
    signal,
    decode: list(parseMessageShare)
  })
export const revokeMessageShare = (shareId: string, signal: AbortSignal) =>
  http.request(`/chat/shares/${encodeURIComponent(shareId)}`, {
    method: 'DELETE',
    body: {},
    signal,
    decode: decodeVoid
  })
export const getSharedMessage = (token: string, signal: AbortSignal) =>
  http.request(`/shared/messages/${encodeURIComponent(token)}`, {
    signal,
    decode: parseSharedMessage
  })
