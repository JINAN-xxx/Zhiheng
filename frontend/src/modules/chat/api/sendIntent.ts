import { generationRequest } from '../generationSettings.ts'
import type { ChatGenerationRequest } from '../generationSettings.ts'

export interface SendInput extends ChatGenerationRequest {
  readonly conversationId: string | null
  readonly model_id: string
  readonly message: string
  readonly skill_slugs?: readonly string[]
  readonly file_ids?: readonly string[]
  readonly knowledge_base_ids?: readonly string[]
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
    (previous.system_prompt ?? '') === (input.system_prompt ?? '') &&
    (previous.temperature ?? null) === (input.temperature ?? null) &&
    (previous.max_output_tokens ?? null) === (input.max_output_tokens ?? null) &&
    (previous.skill_slugs ?? []).length === (input.skill_slugs ?? []).length &&
    (previous.skill_slugs ?? []).every((slug, index) => slug === input.skill_slugs?.[index]) &&
    (previous.file_ids ?? []).length === (input.file_ids ?? []).length &&
    (previous.file_ids ?? []).every((id, index) => id === input.file_ids?.[index]) &&
    (previous.knowledge_base_ids ?? []).length === (input.knowledge_base_ids ?? []).length &&
    (previous.knowledge_base_ids ?? []).every(
      (id, index) => id === input.knowledge_base_ids?.[index]
    )
  )
    return previous
  const { system_prompt = '', temperature = null, max_output_tokens = null, ...base } = input
  return {
    ...base,
    ...generationRequest({ system_prompt, temperature, max_output_tokens }),
    ...(input.skill_slugs ? { skill_slugs: [...input.skill_slugs] } : {}),
    ...(input.file_ids ? { file_ids: [...input.file_ids] } : {}),
    ...(input.knowledge_base_ids ? { knowledge_base_ids: [...input.knowledge_base_ids] } : {}),
    client_request_id: createId()
  }
}
