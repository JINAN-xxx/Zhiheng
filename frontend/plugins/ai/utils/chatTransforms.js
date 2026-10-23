export function normalizeChatSession(source = {}) {
  const sessionId = source.sessionId ?? source.session_id ?? source.id ?? null
  return {
    ...source,
    sessionId,
    title: source.title || source.name || '新对话',
    updatedAt: source.updatedAt ?? source.updated_at ?? source.createdAt ?? source.created_at ?? null,
  }
}

export function normalizeChatSessions(source) {
  const rows = Array.isArray(source)
    ? source
    : source?.rows || source?.data?.rows || source?.data?.items || source?.items || []
  return rows.filter(Boolean).map(normalizeChatSession)
}

export function resolveInitialSessionId(sessions = [], preferredId = null) {
  if (preferredId && sessions.some((session) => session.sessionId === preferredId)) return preferredId
  return sessions[0]?.sessionId || null
}

export function chatMessageLabel(role) {
  return role === 'user' ? '我' : '智衡'
}

export function beginSend() {
  return { runId: null, status: 'connecting' }
}

export function legacyKnowledgeLabels(message = {}) {
  const names = Array.isArray(message.knowledgeBaseNames)
    ? message.knowledgeBaseNames.filter(value => typeof value === 'string' && value.trim()) : []
  if (names.length) return names
  const ids = Array.isArray(message.knowledgeBaseIds) ? message.knowledgeBaseIds : []
  return ids.filter(value => typeof value === 'string' && value.trim())
    .map(id => `历史知识库 ${id.length > 24 ? id.slice(0, 24) + '…' : id}`)
}

export function resolveChatModelOptions(models = []) {
  if (!Array.isArray(models)) return []
  return models
    .filter((model) => model?.modelType === 'chat')
    .map((model) => {
      const title = model.title || model.modelCode
      return {
        label: title,
        title,
        modelCode: model.modelCode,
        value: model.modelId,
      }
    })
}
