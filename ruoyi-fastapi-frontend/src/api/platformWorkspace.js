import request from '@/utils/request'

export function listRecentSessions() {
  return request({
    url: '/ai/chat/session/list',
    method: 'get',
  })
}

export function renameRecentSession(sessionId, title) {
  return request({
    url: `/ai/chat/session/${encodeURIComponent(String(sessionId))}`,
    method: 'patch',
    data: { title },
  })
}

export function deleteRecentSession(sessionId) {
  return request({
    url: `/ai/chat/session/${encodeURIComponent(String(sessionId))}`,
    method: 'delete',
  })
}
