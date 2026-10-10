import request from '@/utils/request'

// Shared session contract for the chat page and platform workspace.
const sessionPath = sessionId => `/ai/chat/session/${encodeURIComponent(String(sessionId))}`

export function listChatSessions(params = {}) {
  return request({
    url: '/ai/chat/session/list',
    method: 'get',
    params,
  })
}

export function getChatSession(sessionId) {
  return request({
    url: sessionPath(sessionId),
    method: 'get',
  })
}

export function renameChatSession(sessionId, title) {
  return request({
    url: sessionPath(sessionId),
    method: 'patch',
    data: { title },
  })
}

export function setChatSessionStatus(sessionId, status) {
  return request({
    url: `${sessionPath(sessionId)}/status`,
    method: 'patch',
    data: { status },
  })
}

export function deleteChatSession(sessionId) {
  return request({
    url: sessionPath(sessionId),
    method: 'delete',
  })
}
