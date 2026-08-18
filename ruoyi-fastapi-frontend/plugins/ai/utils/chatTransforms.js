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
