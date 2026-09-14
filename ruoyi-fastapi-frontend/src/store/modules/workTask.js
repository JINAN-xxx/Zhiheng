import {
  addWorkTaskMember,
  cancelWorkTaskRun,
  createWorkTask,
  createWorkTaskRunStream,
  deleteWorkTask,
  getWorkTask,
  listAvailableWorkAgents,
  listWorkTasks,
  removeWorkTaskMember,
  updateWorkTask,
  updateWorkTaskMemberVersion,
} from '../../../plugins/agent_extension/api/workTask.js'
import {
  createWorkTaskState,
  normalizeWorkTaskDetail,
  normalizeWorkTaskList,
  shouldKeepObservingWorkRun,
} from '../../../plugins/agent_extension/utils/workTask.js'
import { registerWorkTaskSessionCleanup } from '../../../plugins/agent_extension/utils/workTaskSession.js'

const TERMINAL_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
const POLL_INTERVAL = 500

const delay = (milliseconds, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, milliseconds)
  signal?.addEventListener('abort', () => {
    clearTimeout(timer)
    reject(new DOMException('Aborted', 'AbortError'))
  }, { once: true })
})

const useWorkTaskStore = defineStore('workTask', {
  state: () => ({
    tasks: [],
    total: 0,
    pageNum: 1,
    pageSize: 20,
    listQuery: { keyword: '', status: '', pageNum: 1, pageSize: 20 },
    listLoading: false,
    listError: '',
    currentTask: null,
    detailLoading: false,
    detailError: '',
    availableAgents: [],
    agentsLoading: false,
    agentsError: '',
    agentsGeneration: 0,
    createLoading: false,
    createGeneration: 0,
    createAttempt: null,
    createDraft: { goal: '', agentIds: [], activeAgentId: null },
    draftMessage: '',
    retryAttempt: null,
    listGeneration: 0,
    detailGeneration: 0,
    runGeneration: 0,
    runId: null,
    runState: 'idle',
    runError: '',
    streamController: null,
    observeController: null,
    workflow: createWorkTaskState(),
  }),
  getters: {
    runLocked: (state) => Boolean(state.currentTask?.currentRunId)
      || ['pending', 'streaming', 'observing', 'cancelling'].includes(state.runState),
    activeMembers: (state) => (state.currentTask?.members || []).filter((member) => !member.removedAt),
  },
  actions: {
    async loadTasks(query = {}) {
      const generation = ++this.listGeneration
      const nextQuery = { ...this.listQuery, ...query }
      this.listQuery = nextQuery
      this.listLoading = true
      this.listError = ''
      try {
        const response = await listWorkTasks({
          keyword: nextQuery.keyword || undefined,
          status: nextQuery.status || undefined,
          pageNum: nextQuery.pageNum || this.pageNum,
          pageSize: nextQuery.pageSize || this.pageSize,
        })
        if (generation !== this.listGeneration) return false
        const page = normalizeWorkTaskList(response)
        this.tasks = page.rows
        this.total = page.total
        this.pageNum = page.pageNum
        this.pageSize = page.pageSize
        this.listQuery = { ...nextQuery, pageNum: page.pageNum, pageSize: page.pageSize }
        return true
      } catch (error) {
        if (generation !== this.listGeneration) return false
        this.listError = error?.message || '工作列表刷新失败'
        return false
      } finally {
        if (generation === this.listGeneration) this.listLoading = false
      }
    },
    async loadAvailableAgents() {
      const generation = ++this.agentsGeneration
      this.agentsLoading = true
      this.agentsError = ''
      try {
        const response = await listAvailableWorkAgents()
        if (generation !== this.agentsGeneration) return []
        this.availableAgents = Array.isArray(response?.data) ? response.data : []
        return this.availableAgents
      } catch (error) {
        if (generation !== this.agentsGeneration) return []
        this.agentsError = error?.message || '智能体列表加载失败'
        throw error
      } finally {
        if (generation === this.agentsGeneration) this.agentsLoading = false
      }
    },
    async loadTask(taskId, { observe = true } = {}) {
      if (this.currentTask?.taskId !== taskId) this.stopLocalActivity()
      const generation = this.workflow.beginDetailRequest(taskId)
      this.detailGeneration = generation
      this.detailLoading = true
      this.detailError = ''
      try {
        const response = await getWorkTask(taskId)
        if (generation !== this.detailGeneration) return false
        if (!this.workflow.applyDetail(generation, response?.data)) return false
        this.currentTask = this.workflow.currentTask
        this.runId = this.currentTask?.currentRunId || null
        this.retryAttempt = null
        this.draftMessage = ''
        this.runError = ''
        this.runState = this.currentTask?.currentRunId ? 'observing' : 'idle'
        this.restoreRetryFromDetail()
        if (observe && this.currentTask?.currentRunId) {
          void this.observeUntilReleased(taskId, this.runGeneration)
        }
        return true
      } catch (error) {
        if (generation !== this.detailGeneration) return false
        this.detailError = error?.message || '工作详情加载失败'
        return false
      } finally {
        if (generation === this.detailGeneration) this.detailLoading = false
      }
    },
    restoreRetryFromDetail() {
      const task = this.currentTask
      if (!task || task.status !== 'active' || task.currentRunId) return
      const messages = task.messages || []
      const lastMessage = messages.at(-1)
      let userMessage = null
      let agentId = task.activeAgentId
      let kind = 'uncertain'
      if (lastMessage?.role === 'user' && lastMessage.clientMessageId) {
        userMessage = lastMessage
        kind = 'unstarted'
        this.runError = '这条工作目标尚未执行，可以继续执行。'
      } else if (lastMessage?.role === 'assistant' && ['failed', 'cancelled', 'interrupted'].includes(lastMessage.runStatus)) {
        userMessage = messages.find((item) => item.messageId === lastMessage.replyToMessageId)
        agentId = lastMessage.agentId || agentId
        kind = 'failed'
        this.runError = lastMessage.errorSnapshot?.message || lastMessage.errorSnapshot?.msg || `上次执行${lastMessage.runStatus === 'failed' ? '失败' : '未完成'}，可以重试。`
      }
      if (!userMessage?.clientMessageId) return
      this.retryAttempt = {
        agentId,
        message: userMessage.content,
        clientMessageId: userMessage.clientMessageId,
        kind,
      }
      this.draftMessage = userMessage.content
      this.runState = kind === 'failed' ? 'failed' : 'idle'
    },
    async createTask(draft) {
      const generation = ++this.createGeneration
      const payload = this.workflow.prepareCreate(draft)
      this.createAttempt = this.workflow.createAttempt
      this.createDraft = this.workflow.draft
      this.createLoading = true
      try {
        const response = await createWorkTask(payload)
        if (generation !== this.createGeneration) return false
        this.workflow.recordCreated(response?.data, {
          agentId: draft.activeAgentId,
          message: draft.goal,
          clientMessageId: payload.clientMessageId,
        })
        this.currentTask = this.workflow.currentTask
        this.createAttempt = this.workflow.createAttempt
        this.retryAttempt = {
          agentId: draft.activeAgentId,
          message: draft.goal,
          clientMessageId: payload.clientMessageId,
          kind: 'uncertain',
        }
        return { task: this.currentTask, run: { ...this.retryAttempt } }
      } finally {
        if (generation === this.createGeneration) this.createLoading = false
      }
    },
    ensureLocalMessage(message) {
      if (!this.currentTask) return
      const existing = this.currentTask.messages.find((item) => item.clientMessageId === message.clientMessageId)
      if (!existing) this.currentTask.messages.push(message)
    },
    ensureAssistantMessage(data) {
      if (!this.currentTask || !data.messageId) return null
      let message = this.currentTask.messages.find((item) => item.messageId === data.messageId)
      if (!message) {
        message = {
          messageId: data.messageId,
          taskId: this.currentTask.taskId,
          role: 'assistant',
          content: '',
          reasoningContent: '',
          agentId: data.agentId,
          agentNameSnapshot: data.agentName,
          publishedVersion: data.publishedVersion,
          runId: data.runId,
          runStatus: 'streaming',
          createdAt: new Date().toISOString(),
        }
        this.currentTask.messages.push(message)
      }
      return message
    },
    applyRunEvent(event, generation) {
      if (generation !== this.runGeneration || !this.currentTask) return false
      const data = event?.data || {}
      if (data.taskId && data.taskId !== this.currentTask.taskId) return false
      this.workflow.currentTask = this.currentTask
      const workflowResult = this.workflow.applyRunEvent(event, generation)
      if (!workflowResult) return false
      let message = data.messageId
        ? this.currentTask.messages.find((item) => item.messageId === data.messageId)
        : null
      if (event.event === 'meta') {
        this.runId = data.runId
        this.currentTask.currentRunId = data.runId
        this.tasks = this.tasks.map((task) => task.taskId === this.currentTask.taskId
          ? { ...task, currentRunId: data.runId }
          : task)
        this.runState = 'streaming'
        message = this.ensureAssistantMessage(data)
      } else if (event.event === 'content' && message) {
        message.content = `${message.content || ''}${data.delta || ''}`
      } else if (event.event === 'reasoning' && message) {
        message.reasoningContent = `${message.reasoningContent || ''}${data.delta || ''}`
      } else if (event.event === 'usage' && message) {
        message.usageSnapshot = { ...data }
      } else if (event.event === 'error') {
        this.runState = 'failed'
        this.runError = data.message || data.msg || '工作执行失败'
        this.retryAttempt = { ...this.retryAttempt, kind: 'failed' }
        if (message) {
          message.runStatus = 'failed'
          message.errorSnapshot = { ...data }
        }
        return { applied: true, observe: true }
      } else if (event.event === 'done') {
        this.runState = ['pending', 'streaming'].includes(data.status) ? 'observing' : (data.status || 'completed')
        if (['failed', 'cancelled', 'interrupted'].includes(data.status)) {
          this.retryAttempt = { ...this.retryAttempt, kind: 'failed' }
        }
        if (message) message.runStatus = data.status || 'completed'
        return { applied: true, observe: true }
      }
      return { applied: true, observe: false }
    },
    async runMessage({ agentId, message, clientMessageId = crypto.randomUUID(), retryKind = 'fresh' }) {
      if (!this.currentTask || this.runLocked) throw new Error('当前工作仍在执行，请稍候')
      const taskId = this.currentTask.taskId
      this.workflow.currentTask = this.currentTask
      const generation = this.workflow.beginRun()
      this.runGeneration = generation
      const attempt = { agentId, message, clientMessageId, kind: retryKind }
      this.retryAttempt = attempt
      this.draftMessage = message
      this.runState = 'pending'
      this.runError = ''
      this.ensureLocalMessage({
        messageId: `local-${clientMessageId}`,
        taskId,
        clientMessageId,
        role: 'user',
        content: message,
        runStatus: 'pending',
        createdAt: new Date().toISOString(),
      })
      this.streamController?.abort()
      const controller = new AbortController()
      this.streamController = controller
      let shouldObserve = false
      let caughtError = null
      try {
        const stream = await createWorkTaskRunStream(taskId, attempt, controller.signal)
        for await (const event of stream) {
          const result = this.applyRunEvent(event, generation)
          if (result?.observe) shouldObserve = true
        }
        if (generation === this.runGeneration && !TERMINAL_RUN_STATUSES.has(this.runState)) shouldObserve = true
      } catch (error) {
        if (error?.name === 'AbortError' || generation !== this.runGeneration) return false
        this.runState = 'failed'
        this.runError = error?.message || '工作执行失败'
        this.retryAttempt = { ...attempt, kind: 'uncertain' }
        shouldObserve = true
        caughtError = error
      } finally {
        if (generation === this.runGeneration) this.streamController = null
      }
      if (shouldObserve && generation === this.runGeneration) {
        const observed = await this.observeUntilReleased(taskId, generation)
        if (caughtError && (!observed || this.runState === 'failed')) throw caughtError
      }
      return this.runState === 'completed'
    },
    async retryRun({ confirmedFailed = false } = {}) {
      if (!this.retryAttempt) return false
      const attempt = {
        ...this.retryAttempt,
        clientMessageId: confirmedFailed ? crypto.randomUUID() : this.retryAttempt.clientMessageId,
        retryKind: confirmedFailed ? 'failed' : 'uncertain',
      }
      return this.runMessage(attempt)
    },
    async observeUntilReleased(taskId, generation = this.runGeneration) {
      this.observeController?.abort()
      const controller = new AbortController()
      this.observeController = controller
      if (!TERMINAL_RUN_STATUSES.has(this.runState) && this.runState !== 'cancelling') this.runState = 'observing'
      try {
        while (!controller.signal.aborted && generation === this.runGeneration) {
          const response = await getWorkTask(taskId)
          if (controller.signal.aborted || generation !== this.runGeneration) return false
          const detail = normalizeWorkTaskDetail(response?.data)
          if (!detail || detail.taskId !== taskId) return false
          this.currentTask = detail
          this.workflow.currentTask = detail
          this.runId = detail.currentRunId || this.runId || null
          if (detail.currentRunId) {
            this.tasks = this.tasks.map((task) => task.taskId === detail.taskId
              ? { ...task, currentRunId: detail.currentRunId }
              : task)
          }
          if (!detail.currentRunId) {
            const attemptedUser = detail.messages.find((item) => (
              item.role === 'user' && item.clientMessageId === this.retryAttempt?.clientMessageId
            ))
            const lastRun = [...detail.messages].reverse().find((item) => (
              item.role === 'assistant'
              && item.runId
              && (!attemptedUser || item.replyToMessageId === attemptedUser.messageId)
            ))
            if (shouldKeepObservingWorkRun({
              currentRunId: detail.currentRunId,
              runStatus: lastRun?.runStatus,
              cancelling: this.runState === 'cancelling',
            })) {
              this.runState = 'observing'
              this.workflow.runState = 'observing'
              await delay(POLL_INTERVAL, controller.signal)
              continue
            }
            if (this.runState === 'cancelling') this.runState = 'cancelled'
            else if (lastRun?.runStatus) this.runState = lastRun.runStatus
            else if (!TERMINAL_RUN_STATUSES.has(this.runState)) this.runState = 'failed'
            this.workflow.runState = this.runState
            this.workflow.applyObservedDetail(detail)
            const summary = { ...detail }
            delete summary.members
            delete summary.messages
            this.tasks = this.tasks.map((task) => task.taskId === detail.taskId
              ? { ...task, ...summary }
              : task)
            void this.loadTasks({ pageNum: this.pageNum, pageSize: this.pageSize })
            return Boolean(lastRun) || this.runState === 'cancelled'
          }
          await delay(POLL_INTERVAL, controller.signal)
        }
      } catch (error) {
        if (error?.name !== 'AbortError' && generation === this.runGeneration) {
          this.runError = error?.message || '工作状态刷新失败'
        }
      } finally {
        if (this.observeController === controller) this.observeController = null
      }
      return false
    },
    async cancelRun() {
      if (!this.currentTask || !this.runId) return false
      const taskId = this.currentTask.taskId
      const generation = this.runGeneration
      await cancelWorkTaskRun(taskId, this.runId)
      if (generation !== this.runGeneration || this.currentTask?.taskId !== taskId) return false
      this.workflow.currentTask = this.currentTask
      this.workflow.requestCancel()
      this.runState = 'cancelling'
      await this.observeUntilReleased(taskId, generation)
      return true
    },
    async saveTask(changes) {
      const taskId = this.currentTask.taskId
      const generation = this.workflow.beginDetailRequest(taskId)
      this.detailGeneration = generation
      const response = await updateWorkTask(taskId, {
        expectedVersion: this.currentTask.version, ...changes,
      })
      if (generation !== this.detailGeneration || this.currentTask?.taskId !== taskId) return false
      this.currentTask = normalizeWorkTaskDetail(response?.data)
      return this.currentTask
    },
    async addMember(agentId) {
      const taskId = this.currentTask.taskId
      const generation = this.workflow.beginDetailRequest(taskId)
      this.detailGeneration = generation
      const response = await addWorkTaskMember(taskId, {
        agentId, expectedVersion: this.currentTask.version,
      })
      if (generation !== this.detailGeneration || this.currentTask?.taskId !== taskId) return false
      this.currentTask = normalizeWorkTaskDetail(response?.data)
    },
    async upgradeMember(member) {
      const taskId = this.currentTask.taskId
      const generation = this.workflow.beginDetailRequest(taskId)
      this.detailGeneration = generation
      const response = await updateWorkTaskMemberVersion(taskId, member.agentId, {
        expectedVersion: this.currentTask.version,
        expectedPublishedVersion: member.latestPublishedVersion,
      })
      if (generation !== this.detailGeneration || this.currentTask?.taskId !== taskId) return false
      this.currentTask = normalizeWorkTaskDetail(response?.data)
    },
    async removeMember(agentId) {
      const taskId = this.currentTask.taskId
      const generation = this.workflow.beginDetailRequest(taskId)
      this.detailGeneration = generation
      const response = await removeWorkTaskMember(taskId, agentId, this.currentTask.version)
      if (generation !== this.detailGeneration || this.currentTask?.taskId !== taskId) return false
      this.currentTask = normalizeWorkTaskDetail(response?.data)
    },
    async removeTask(taskId) {
      const generation = this.listGeneration
      await deleteWorkTask(taskId)
      if (generation !== this.listGeneration) return false
      this.tasks = this.tasks.filter((task) => task.taskId !== taskId)
      if (this.currentTask?.taskId === taskId) this.clearCurrentTask()
    },
    async renameListedTask(task, title) {
      const generation = this.listGeneration
      const response = await updateWorkTask(task.taskId, { expectedVersion: task.version, title })
      if (generation !== this.listGeneration) return false
      const updated = response?.data || {}
      this.tasks = this.tasks.map((item) => item.taskId === task.taskId ? { ...item, ...updated } : item)
      if (this.currentTask?.taskId === task.taskId) this.currentTask = normalizeWorkTaskDetail(updated)
    },
    stopLocalActivity() {
      this.workflow.invalidateRun()
      this.runGeneration = this.workflow.runGeneration
      this.workflow.detailGeneration += 1
      this.detailGeneration = this.workflow.detailGeneration
      this.createGeneration += 1
      this.createLoading = false
      this.streamController?.abort()
      this.observeController?.abort()
      this.streamController = null
      this.observeController = null
    },
    clearCurrentTask() {
      this.stopLocalActivity()
      this.currentTask = null
      this.runId = null
      this.runState = 'idle'
      this.runError = ''
      this.draftMessage = ''
      this.retryAttempt = null
    },
    resetForSession() {
      this.stopLocalActivity()
      this.listGeneration += 1
      this.detailGeneration += 1
      this.agentsGeneration += 1
      this.createGeneration += 1
      this.tasks = []
      this.total = 0
      this.listQuery = { keyword: '', status: '', pageNum: 1, pageSize: 20 }
      this.currentTask = null
      this.availableAgents = []
      this.agentsLoading = false
      this.agentsError = ''
      this.createAttempt = null
      this.createLoading = false
      this.createDraft = { goal: '', agentIds: [], activeAgentId: null }
      this.draftMessage = ''
      this.retryAttempt = null
      this.runId = null
      this.runState = 'idle'
      this.runError = ''
      const workflow = createWorkTaskState()
      workflow.listGeneration = this.listGeneration
      workflow.detailGeneration = this.detailGeneration
      workflow.runGeneration = this.runGeneration
      this.workflow = workflow
    },
  },
})

registerWorkTaskSessionCleanup(() => {
  const store = useWorkTaskStore()
  store.resetForSession()
})

export default useWorkTaskStore
