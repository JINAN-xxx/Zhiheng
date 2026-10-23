export interface SendInput {
  readonly conversationId: string | null
  readonly model_id: string
  readonly message: string
}
export interface SendIntent extends SendInput {
  readonly client_request_id: string
}
export function nextSendIntent(
  previous: SendIntent | null,
  input: SendInput,
  createId: () => string
): SendIntent {
  if (
    previous &&
    previous.conversationId === input.conversationId &&
    previous.model_id === input.model_id &&
    previous.message === input.message
  )
    return previous
  return { ...input, client_request_id: createId() }
}
