import request from '@/utils/request'

export function listRecentSessions(params = {}) {
  return request({
    url: '/ai/chat/session/list',
    method: 'get',
    params,
  })
}

export function setRecentSessionStatus(sessionId, status) {
  return request({
    url: `/ai/chat/session/${encodeURIComponent(String(sessionId))}/status`,
    method: 'patch',
    data: { status },
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
