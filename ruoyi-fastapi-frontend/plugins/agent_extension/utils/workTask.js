const WORK_STATUSES = new Set(['active', 'completed', 'archived'])
const RUNNING_STATES = new Set(['pending', 'streaming', 'observing', 'cancelling'])

const hasPermission = (permissions, permission) => (
  permissions.has('*:*:*') || permissions.has(permission)
)

const textValidation = (value, max, label) => {
  const normalized = typeof value === 'string' ? value.trim() : ''
  if (!normalized) return { valid: false, message: `${label}不能为空` }
  if (normalized.length > max) return { valid: false, message: `${label}不能超过 ${max} 个字符` }
  return { valid: true, value: normalized }
}

export const validateWorkTitle = (value) => textValidation(value, 100, '标题')
export const validateWorkGoal = (value) => textValidation(value, 32000, '工作目标')
export const validateWorkMessage = (value) => textValidation(value, 32000, '工作消息')

export function validateWorkMembers(agentIds) {
  const uniqueIds = [...new Set((Array.isArray(agentIds) ? agentIds : []).filter(Boolean))]
  if (uniqueIds.length < 1) return { valid: false, message: '请至少添加 1 个智能体' }
  if (uniqueIds.length > 10) return { valid: false, message: '最多添加 10 个智能体' }
  return { valid: true, value: uniqueIds }
}

export function resolveWorkTaskCapabilities(rawPermissions = new Set()) {
  const permissions = rawPermissions instanceof Set ? rawPermissions : new Set(rawPermissions || [])
  const list = hasPermission(permissions, 'agent_extension:work:list')
  const query = hasPermission(permissions, 'agent_extension:work:query')
  const add = hasPermission(permissions, 'agent_extension:work:add')
  const edit = hasPermission(permissions, 'agent_extension:work:edit')
  const remove = hasPermission(permissions, 'agent_extension:work:remove')
  const run = hasPermission(permissions, 'agent_extension:work:run')
  const useAgent = hasPermission(permissions, 'agent_extension:agent:use')
  return {
    visible: list || query || add,
    list,
    query,
    add,
    edit,
    remove,
    useAgent,
    canCreate: add && useAgent,
    canRun: run && useAgent,
    canManageMembers: edit && useAgent,
  }
}

export const buildCreateWorkTaskPayload = (source = {}) => ({
  goal: source.goal,
  agentIds: [...(source.agentIds || [])],
  activeAgentId: source.activeAgentId,
  clientMessageId: source.clientMessageId,
})

export const buildRunWorkTaskPayload = (source = {}) => ({
  agentId: source.agentId,
  clientMessageId: source.clientMessageId,
  message: source.message,
})

export const buildUpdateWorkTaskPayload = (source = {}) => {
  const payload = { expectedVersion: source.expectedVersion }
  for (const field of ['title', 'goal', 'status']) {
    if (source[field] !== undefined) payload[field] = source[field]
  }
  return payload
}

export const normalizeWorkTaskList = (response = {}) => ({
  rows: Array.isArray(response.rows) ? response.rows.filter(Boolean) : [],
  total: Number(response.total) || 0,
  pageNum: Number(response.pageNum) || 1,
  pageSize: Number(response.pageSize) || 20,
})

export function normalizeWorkTaskDetail(detail) {
  if (!detail || typeof detail !== 'object') return null
  return {
    ...detail,
    status: WORK_STATUSES.has(detail.status) ? detail.status : 'active',
    members: Array.isArray(detail.members) ? detail.members.map((member) => ({ ...member })) : [],
    // The server sequence is authoritative. DTOs intentionally omit that internal field.
    messages: Array.isArray(detail.messages) ? detail.messages.map((message) => ({ ...message })) : [],
  }
}

export const activeWorkMembers = (task) => (
  (task?.members || []).filter((member) => !member.removedAt)
)

export const usableWorkMembers = (task) => (
  activeWorkMembers(task).filter((member) => member.available)
)

export const resolveConversationMode = (path) => (
  path === '/ai/work' || path.startsWith('/ai/work/') ? 'work' : 'chat'
)

const createSignature = (draft) => JSON.stringify({
  goal: draft.goal?.trim(),
  agentIds: [...(draft.agentIds || [])],
  activeAgentId: draft.activeAgentId || null,
})

export function createWorkTaskState({ createId = () => crypto.randomUUID() } = {}) {
  return {
    tasks: [],
    currentTask: null,
    draft: { goal: '', agentIds: [], activeAgentId: null },
    draftMessage: '',
    listGeneration: 0,
    detailGeneration: 0,
    runGeneration: 0,
    runId: null,
    runState: 'idle',
    runError: null,
    createAttempt: null,
    retryAttempt: null,
    cancelRequested: false,

    prepareCreate(draft) {
      const signature = createSignature(draft)
      if (!this.createAttempt || this.createAttempt.signature !== signature || this.createAttempt.created) {
        this.createAttempt = { signature, clientMessageId: createId(), created: false }
      }
      this.draft = {
        goal: draft.goal,
        agentIds: [...draft.agentIds],
        activeAgentId: draft.activeAgentId,
      }
      return buildCreateWorkTaskPayload({ ...draft, clientMessageId: this.createAttempt.clientMessageId })
    },
    recordCreateFailure(error) {
      this.runError = error
      return this.draft
    },
    recordCreated(task, firstRun) {
      this.currentTask = normalizeWorkTaskDetail(task)
      if (this.createAttempt) this.createAttempt.created = true
      this.retryAttempt = firstRun ? { ...firstRun } : null
      if (firstRun?.message || firstRun?.goal) this.draft.goal = firstRun.message || firstRun.goal
      this.runError = null
      return this.currentTask
    },
    shouldCreateTask() {
      return !this.currentTask?.taskId
    },
    recordRunFailure(error, attempt) {
      this.runState = 'failed'
      this.runError = error
      this.retryAttempt = { ...attempt }
      this.draftMessage = attempt.message || ''
    },
    prepareRunRetry({ kind }) {
      if (!this.retryAttempt) return null
      if (kind === 'failed') this.retryAttempt.clientMessageId = createId()
      return { ...this.retryAttempt }
    },
    beginListRequest() {
      return ++this.listGeneration
    },
    applyList(generation, response) {
      if (generation !== this.listGeneration) return false
      this.tasks = normalizeWorkTaskList(response).rows
      return true
    },
    beginDetailRequest() {
      return ++this.detailGeneration
    },
    applyDetail(generation, detail) {
      if (generation !== this.detailGeneration) return false
      this.currentTask = normalizeWorkTaskDetail(detail)
      return true
    },
    beginRun(runId = null) {
      const generation = ++this.runGeneration
      this.runId = runId
      this.runState = 'pending'
      this.runError = null
      this.cancelRequested = false
      return generation
    },
    invalidateRun() {
      this.runGeneration += 1
    },
    applyRunEvent(streamEvent, generation = this.runGeneration) {
      if (generation !== this.runGeneration) return false
      const { event, data = {} } = streamEvent || {}
      if (data.taskId && this.currentTask?.taskId && data.taskId !== this.currentTask.taskId) return false
      if (event === 'meta') {
        this.runId = data.runId || this.runId
        this.runState = 'streaming'
      } else if (event === 'error') {
        this.runState = 'failed'
        this.runError = data
      } else if (event === 'done') {
        if (data.status === 'pending' || data.status === 'streaming') {
          this.runState = 'observing'
          return { applied: true, observe: true }
        }
        this.runState = data.status || 'completed'
        return { applied: true, observe: true }
      }
      return { applied: true, observe: false }
    },
    requestCancel() {
      if (this.isRunLocked()) {
        this.cancelRequested = true
        this.runState = 'cancelling'
      }
    },
    applyObservedDetail(detail) {
      this.currentTask = normalizeWorkTaskDetail(detail)
      if (detail?.currentRunId) {
        if (this.runState !== 'cancelling') this.runState = 'observing'
        this.runId = detail.currentRunId
        return { released: false }
      }
      if (this.runState === 'cancelling') this.runState = 'cancelled'
      else if (RUNNING_STATES.has(this.runState)) this.runState = 'completed'
      this.runId = null
      return { released: true }
    },
    isRunLocked() {
      return Boolean(this.currentTask?.currentRunId) || RUNNING_STATES.has(this.runState)
    },
  }
}
