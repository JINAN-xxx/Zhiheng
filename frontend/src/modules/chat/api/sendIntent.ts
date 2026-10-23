export interface SendInput {
  readonly conversationId: string | null
  readonly model_id: string
  readonly message: string
  readonly skill_slugs?: readonly string[]
  readonly file_ids?: readonly string[]
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
    previous.message === input.message &&
    (previous.skill_slugs ?? []).length === (input.skill_slugs ?? []).length &&
    (previous.skill_slugs ?? []).every((slug, index) => slug === input.skill_slugs?.[index]) &&
    (previous.file_ids ?? []).length === (input.file_ids ?? []).length &&
    (previous.file_ids ?? []).every((id, index) => id === input.file_ids?.[index])
  )
    return previous
  return {
    ...input,
    ...(input.skill_slugs ? { skill_slugs: [...input.skill_slugs] } : {}),
    ...(input.file_ids ? { file_ids: [...input.file_ids] } : {}),
    client_request_id: createId()
  }
}
