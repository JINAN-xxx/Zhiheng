import { deleteRecentSession, listRecentSessions, renameRecentSession, setRecentSessionStatus } from '@/api/platformWorkspace'
import {
  normalizeWorkspaceSessions,
  shouldApplyGeneration,
} from '@/components/Workspace/workspaceTransforms.js'

const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    sessions: [],
    sessionsLoading: false,
    sessionsError: '',
    sessionsLastSuccessAt: null,
    sessionGeneration: 0,
    sessionStatus: 'active',
    sessionsPageNum: 1,
    sessionsPageSize: 20,
    sessionsTotal: 0,
    sessionMutatingIds: [],
    sessionChange: null,
    sessionStatusChanges: {},
  }),
  actions: {
    async loadSessions({ status = this.sessionStatus, pageNum = this.sessionsPageNum } = {}) {
      if (status !== this.sessionStatus) {
        this.sessions = []
        this.sessionsTotal = 0
      }
      this.sessionStatus = status
      this.sessionsPageNum = pageNum
      const requestGeneration = ++this.sessionGeneration
      this.sessionsLoading = true
      this.sessionsError = ''

      try {
        const response = await listRecentSessions({ status, pageNum, pageSize: this.sessionsPageSize })
        if (!shouldApplyGeneration(requestGeneration, this.sessionGeneration)) return false
        this.sessions = normalizeWorkspaceSessions(response)
        this.sessionsTotal = Number(response?.total ?? response?.data?.total ?? this.sessions.length)
        this.sessionsLastSuccessAt = Date.now()
        return true
      } catch {
        if (!shouldApplyGeneration(requestGeneration, this.sessionGeneration)) return false
        this.sessionsError = '会话列表刷新失败'
        return false
      } finally {
        if (shouldApplyGeneration(requestGeneration, this.sessionGeneration)) {
          this.sessionsLoading = false
        }
      }
    },
    async renameSession(sessionId, title) {
      const response = await renameRecentSession(sessionId, title)
      const updated = response?.data || {}
      this.sessions = this.sessions.map((session) => session.sessionId === sessionId
        ? {
            ...session,
            title: updated.title || title,
            updatedAt: updated.updatedAt || new Date().toISOString(),
          }
        : session)
    },
    async deleteSession(sessionId) {
      if (this.sessionMutatingIds.includes(sessionId)) return false
      this.sessionMutatingIds.push(sessionId)
      try {
        await deleteRecentSession(sessionId)
        this.invalidateSessionRequests()
        this.sessions = this.sessions.filter((session) => session.sessionId !== sessionId)
        await this.refreshAfterSessionChange()
        return true
      } finally {
        this.sessionMutatingIds = this.sessionMutatingIds.filter(id => id !== sessionId)
      }
    },
    async setSessionStatus(sessionId, status) {
      if (this.sessionMutatingIds.includes(sessionId)) return null
      this.sessionMutatingIds.push(sessionId)
      try {
        const response = await setRecentSessionStatus(sessionId, status)
        this.invalidateSessionRequests()
        this.sessionChange = { sessionId, status: response.data.status }
        this.sessionStatusChanges[sessionId] = this.sessionChange
        if (response.data.status !== this.sessionStatus) {
          this.sessions = this.sessions.filter(session => session.sessionId !== sessionId)
        }
        await this.refreshAfterSessionChange()
        return response.data
      } finally {
        this.sessionMutatingIds = this.sessionMutatingIds.filter(id => id !== sessionId)
      }
    },
    async refreshAfterSessionChange() {
      const applied = await this.loadSessions()
      if (applied && this.sessions.length === 0 && this.sessionsPageNum > 1) {
        const lastPage = Math.max(1, Math.ceil(this.sessionsTotal / this.sessionsPageSize))
        await this.loadSessions({ pageNum: Math.min(this.sessionsPageNum - 1, lastPage) })
      }
    },
    invalidateSessionRequests() {
      this.sessionGeneration += 1
      this.sessionsLoading = false
    },
  },
})

export default useWorkspaceStore
