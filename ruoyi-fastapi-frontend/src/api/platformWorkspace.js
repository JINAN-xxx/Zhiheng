import request from '@/utils/request'

export function listRecentSessions() {
  return request({
    url: '/ai/chat/session/list',
    method: 'get',
  })
}

export function archiveRecentSession(sessionId) {
  return request({
    url: `/ai/chat/session/${encodeURIComponent(String(sessionId))}`,
    method: 'delete',
  })
}
