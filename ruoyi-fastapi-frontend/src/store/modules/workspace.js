import { archiveRecentSession, listRecentSessions } from '@/api/platformWorkspace'
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
  }),
  actions: {
    async loadSessions() {
      const requestGeneration = ++this.sessionGeneration
      this.sessionsLoading = true
      this.sessionsError = ''

      try {
        const response = await listRecentSessions()
        if (!shouldApplyGeneration(requestGeneration, this.sessionGeneration)) return false
        this.sessions = normalizeWorkspaceSessions(response)
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
    async archiveSession(sessionId) {
      await archiveRecentSession(sessionId)
      this.sessions = this.sessions.filter((session) => session.sessionId !== sessionId)
    },
    invalidateSessionRequests() {
      this.sessionGeneration += 1
      this.sessionsLoading = false
    },
  },
})

export default useWorkspaceStore
