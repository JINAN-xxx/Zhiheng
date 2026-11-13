import type { Agent, AgentConfig, AgentPayload } from './api/index.ts'
export { selectPreviewMessages } from './history.ts'
export interface AgentForm
  extends Omit<AgentConfig, 'temperature' | 'maxTokens' | 'historyRounds' | 'knowledgeTopK'> {
  temperature: number | null
  maxTokens: number | null
  historyRounds: number | null
  knowledgeTopK: number | null
}
export function createAgentForm(value?: AgentConfig): AgentForm {
  return value
    ? { ...value, skillSlugs: [...value.skillSlugs], knowledgeBaseIds: [...value.knowledgeBaseIds] }
    : {
        name: '',
        description: '',
        modelId: '',
        systemPrompt: '',
        welcomeMessage: '',
        skillSlugs: [],
        knowledgeBaseIds: [],
        knowledgeTopK: 8,
        applicationId: null,
        temperature: 0.7,
        maxTokens: 2048,
        historyRounds: 20
      }
}
export function buildAgentPayload(value: AgentConfig | AgentForm): AgentPayload {
  return {
    name: value.name.trim(),
    description: value.description,
    model_id: value.modelId,
    system_prompt: value.systemPrompt,
    welcome_message: value.welcomeMessage,
    skill_slugs: [...value.skillSlugs],
    knowledge_base_ids: [...value.knowledgeBaseIds],
    knowledge_top_k: value.knowledgeTopK ?? Number.NaN,
    application_id: value.applicationId || null,
    temperature: value.temperature ?? Number.NaN,
    max_tokens: value.maxTokens ?? Number.NaN,
    history_rounds: value.historyRounds ?? Number.NaN
  }
}
export const hasUnpublishedAgentChanges = (value: Agent): boolean =>
  value.status === 'published' &&
  value.publishedConfig !== null &&
  JSON.stringify(buildAgentPayload(value)) !==
    JSON.stringify(buildAgentPayload(value.publishedConfig))
export const agentStatusLabel = (value: Agent): string =>
  value.status === 'published'
    ? hasUnpublishedAgentChanges(value)
      ? '已发布 · 有未发布更改'
      : '已发布'
    : '草稿'
export const getPublishedAgentAccess = (value: Agent | null) =>
  value?.status === 'published' && value.publishedConfig?.applicationId
    ? { applicationId: value.publishedConfig.applicationId, modelId: value.publishedConfig.modelId }
    : null
export function withCurrent(
  options: { value: string; label: string; disabled?: boolean }[],
  current: string | string[] | null
) {
  const values = Array.isArray(current) ? current : current ? [current] : []
  return [
    ...options,
    ...values
      .filter((value) => !options.some((option) => option.value === value))
      .map((value) => ({ value, label: value, disabled: !Array.isArray(current) }))
  ]
}
