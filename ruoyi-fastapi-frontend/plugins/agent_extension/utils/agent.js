const read = (source, camel, snake, fallback = '') => source?.[camel] ?? source?.[snake] ?? fallback
const stringList = (value) => Array.isArray(value) ? [...new Set(value.filter((item) => typeof item === 'string').map((item) => item.trim()).filter(Boolean))] : []

export function createAgentForm(source = {}) {
  return {
    name: read(source, 'name', 'name'),
    description: read(source, 'description', 'description'),
    modelId: read(source, 'modelId', 'model_id'),
    systemPrompt: read(source, 'systemPrompt', 'system_prompt'),
    welcomeMessage: read(source, 'welcomeMessage', 'welcome_message'),
    skillSlugs: stringList(read(source, 'skillSlugs', 'skill_slugs', [])),
    knowledgeBaseIds: stringList(read(source, 'knowledgeBaseIds', 'knowledge_base_ids', [])),
    applicationId: read(source, 'applicationId', 'application_id', null) || null,
    temperature: read(source, 'temperature', 'temperature', 0.7),
    maxTokens: read(source, 'maxTokens', 'max_tokens', 2048),
  }
}

export function normalizeAgent(source = {}) {
  const publishedConfig = read(source, 'publishedConfig', 'published_config', null)
  return {
    ...createAgentForm(source),
    agentId: read(source, 'agentId', 'agent_id'),
    ownerUserId: read(source, 'ownerUserId', 'owner_user_id', null),
    version: Number(read(source, 'version', 'version', 1)) || 1,
    status: source?.status === 'published' ? 'published' : 'draft',
    publishedVersion: Number(read(source, 'publishedVersion', 'published_version', 0)) || null,
    publishedConfig: publishedConfig && typeof publishedConfig === 'object' ? createAgentForm(publishedConfig) : null,
    publishedAt: read(source, 'publishedAt', 'published_at', null),
    createdAt: read(source, 'createdAt', 'created_at', null),
    updatedAt: read(source, 'updatedAt', 'updated_at', null),
  }
}

export function normalizeAgentPage(response = {}) {
  const source = response?.data && !Array.isArray(response.data) ? response.data : response
  const rows = Array.isArray(source?.rows) ? source.rows.map(normalizeAgent) : []
  const total = Number(source?.total)
  return { rows, total: Number.isFinite(total) && total >= 0 ? total : rows.length }
}

export function buildAgentPayload(form = {}, expectedVersion) {
  const payload = createAgentForm(form)
  payload.name = payload.name.trim()
  if (expectedVersion !== undefined) payload.expectedVersion = expectedVersion
  return payload
}

export function hasUnpublishedAgentChanges(agent = {}) {
  return agent.status === 'published' && Number(agent.version) > Number(agent.publishedVersion)
}

export function agentStatusLabel(agent = {}) {
  if (hasUnpublishedAgentChanges(agent)) return '已发布 · 有未发布更改'
  return agent.status === 'published' ? '已发布' : '草稿'
}

export function getPublishedAgentAccess(agent = {}) {
  if (agent.status !== 'published') return null
  const { modelId, applicationId } = agent.publishedConfig || {}
  return modelId && applicationId ? { modelId, applicationId } : null
}

export function buildAgentPreviewPayload(messages) {
  if (!Array.isArray(messages) || !messages.length || messages.some((item) => !['user', 'assistant'].includes(item?.role) || typeof item.content !== 'string' || !item.content.trim())) {
    throw new Error('试聊消息无效')
  }
  return { messages: messages.map(({ role, content }) => ({ role, content })), stream: false }
}

export function extractAgentCompletion(response) {
  const completion = response?.data ?? response
  const content = completion?.choices?.[0]?.message?.content
  if (typeof content !== 'string') throw new Error('试聊响应无效')
  return content
}

export function buildAgentPublicPath(agentId) {
  if (typeof agentId !== 'string' || !agentId || agentId === '.' || agentId === '..' || /[\u0000-\u001f\u007f]/u.test(agentId)) throw new Error('智能体标识无效')
  return `/v1/agents/${encodeURIComponent(agentId)}/chat/completions`
}
