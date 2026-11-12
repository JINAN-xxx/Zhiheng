import { createRequestId } from '../../shared/requestId.ts'
import { computed, inject, onBeforeUnmount, provide, reactive, ref } from 'vue'
import type { InjectionKey } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../shared/http.ts'
import type { ContextTicket } from '../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../shared/stream.ts'
import type { RunSnapshot } from '../../shared/stream.ts'
import { identity } from '../identity/public.ts'
import {
  cancelPreparation,
  getPreparation,
  isPreparation,
  observePreparation,
  projectPreparation,
  preparationFinished
} from '../knowledge/public.ts'
import type { KnowledgePreparation } from '../knowledge/public.ts'
import * as api from './api/index.ts'
import type {
  WorkAgent,
  WorkFilters,
  WorkMember,
  WorkStatus,
  WorkSummary,
  WorkTask
} from './api/index.ts'
import { nextCreateIntent, nextWorkIntent } from './presentation.ts'
import type { CreateIntent, WorkDraft, WorkIntent } from './presentation.ts'

function createWorkWorkspace() {
  const tasks = ref<WorkSummary[]>([]),
    total = ref(0)
  const filters = reactive<WorkFilters>({ keyword: '', status: '', pageNum: 1, pageSize: 20 })
  const listLoading = ref(false),
    listError = ref('')
  const currentTask = ref<WorkTask | null>(null),
    detailLoading = ref(false),
    detailError = ref('')
  const availableAgents = ref<WorkAgent[]>([]),
    agentsLoading = ref(false),
    agentsError = ref('')
  const createLoading = ref(false),
    admitting = ref(false),
    mutating = ref(false),
    stopping = ref(false)
  const runError = ref(''),
    disconnected = ref(false),
    activeRun = ref<RunSnapshot | null>(null)
  const activePreparation = ref<KnowledgePreparation | null>(null)
  const stopAcknowledged = computed(() => {
    const preparation = activePreparation.value
    return preparation && !preparationFinished(preparation)
      ? preparation.status === 'cancel_requested'
      : activeRun.value?.status === 'cancel_requested'
  })
  const retryKind = ref<'' | 'unstarted' | 'uncertain' | 'failed'>('')
  const listScope = new RequestScope(),
    taskScope = new RequestScope(),
    agentsScope = new RequestScope()
  const mutationScope = new RequestScope(),
    createScope = new RequestScope(),
    observeScope = new RequestScope()
  const intents = new Map<string, WorkIntent>()
  const pendingStops = reactive(new Set<string>())
  const stopOperations = new Map<string, Promise<void>>()
  // Preserve busy and uncertain admissions across navigation; only newer server facts release them.
  const taskLocks = reactive(new Map<string, number>())
  let pendingCreate: CreateIntent | null = null
  const createDraft = ref<WorkDraft>({ goal: '', agentIds: [], activeAgentId: null })
  const runLocked = computed(
    () =>
      admitting.value ||
      mutating.value ||
      stopping.value ||
      Boolean(currentTask.value && pendingStops.has(currentTask.value.taskId)) ||
      Boolean(currentTask.value?.currentRunId) ||
      Boolean(currentTask.value?.preparationId) ||
      Boolean(activePreparation.value && !preparationFinished(activePreparation.value)) ||
      Boolean(activeRun.value && !isTerminal(activeRun.value))
  )
  const canStop = computed(
    () =>
      Boolean(currentTask.value?.currentRunId) ||
      Boolean(currentTask.value?.preparationId) ||
      Boolean(activePreparation.value && !preparationFinished(activePreparation.value)) ||
      Boolean(activeRun.value && !isTerminal(activeRun.value))
  )
  const submissionBlocked = computed(
    () =>
      runLocked.value ||
      retryKind.value === 'uncertain' ||
      disconnected.value ||
      Boolean(currentTask.value && taskLocks.has(currentTask.value.taskId))
  )
  function report(reason: unknown): string {
    return errorMessage(reason)
  }
  function recordTask(task: WorkSummary): void {
    const lock = taskLocks.get(task.taskId)
    if (lock !== undefined && task.version < lock) return
    if (task.currentRunId || task.preparationId) taskLocks.set(task.taskId, task.version)
    else if (!intents.has(task.taskId)) {
      taskLocks.delete(task.taskId)
      pendingStops.delete(task.taskId)
    }
  }
  function isTaskBusy(taskId: string): boolean {
    return (
      taskLocks.has(taskId) || (currentTask.value?.taskId === taskId && submissionBlocked.value)
    )
  }
  async function loadTasks(query?: WorkFilters): Promise<void> {
    if (query) Object.assign(filters, query)
    const context = listScope.replace()
    listLoading.value = true
    listError.value = ''
    try {
      const result = await api.listTasks({ ...filters }, context.signal)
      if (context.isCurrent()) {
        result.rows.forEach(recordTask)
        tasks.value = result.rows
        total.value = result.total
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) listError.value = report(reason)
    } finally {
      if (context.isCurrent()) listLoading.value = false
    }
  }
  async function loadAvailableAgents(
    taskId: string | null = currentTask.value?.taskId ?? null
  ): Promise<void> {
    const context = agentsScope.replace()
    agentsLoading.value = true
    agentsError.value = ''
    availableAgents.value = []
    try {
      const result = await api.availableAgents(taskId, context.signal)
      if (context.isCurrent()) availableAgents.value = result
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) agentsError.value = report(reason)
    } finally {
      if (context.isCurrent()) agentsLoading.value = false
    }
  }
  function applyRun(run: RunSnapshot, taskId: string): void {
    const task = currentTask.value
    if (
      !task ||
      task.taskId !== taskId ||
      run.resource_type !== 'work_turn' ||
      run.resource_id !== taskId ||
      !acceptSnapshot(activeRun.value, run)
    )
      return
    activeRun.value = run
    if (isTerminal(run) || run.status === 'cancel_requested') pendingStops.delete(taskId)
    const reply = task.messages.find(
      (message) => message.role === 'assistant' && message.runId === run.id
    )
    if (reply)
      Object.assign(reply, {
        content: run.text,
        reasoningContent: run.reasoning ?? '',
        runStatus: run.status,
        usage: run.usage,
        errorSnapshot: run.error
      })
  }
  async function refreshTask(taskId: string, context: ContextTicket): Promise<WorkTask | null> {
    const task = await api.getTask(taskId, context.signal)
    if (!context.isCurrent() || currentTask.value?.taskId !== taskId) return null
    recordTask(task)
    currentTask.value = task
    return task
  }
  async function follow(run: RunSnapshot, taskId: string, parent: ContextTicket): Promise<void> {
    const observation = observeScope.replace()
    const context: ContextTicket = {
      signal: AbortSignal.any([parent.signal, observation.signal]),
      isCurrent: () =>
        parent.isCurrent() && observation.isCurrent() && currentTask.value?.taskId === taskId
    }
    disconnected.value = false
    try {
      if (pendingStops.has(taskId) && !isTerminal(run)) {
        await cancelRun(false)
        if (!context.isCurrent()) return
        if (activeRun.value?.id === run.id) run = activeRun.value
        if (pendingStops.has(taskId)) throw new Error('停止结果待确认，请恢复查看')
      }
      const terminal = await api.observeWorkRun(
        taskId,
        run,
        (snapshot) => {
          if (context.isCurrent()) applyRun(snapshot, taskId)
        },
        context.signal
      )
      if (!context.isCurrent()) return
      applyRun(terminal, taskId)
      const task = await refreshTask(taskId, context)
      if (!task) return
      if (task.currentRunId) throw new Error('运行已结束，任务状态尚未确认，请恢复查看')
      retryKind.value = terminal.status === 'completed' ? '' : 'failed'
      runError.value =
        terminal.error?.message ??
        (terminal.status === 'completed' ? '' : '本轮已停止或中断，可用新请求重试')
      void loadTasks()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = report(reason)
        disconnected.value = true
      }
    }
  }
  function updatePreparation(value: KnowledgePreparation, taskId: string): KnowledgePreparation {
    if (value.resource_type !== 'work_turn' || value.resource_id !== taskId)
      throw new Error('知识检索准备与当前工作不匹配')
    value = projectPreparation(activePreparation.value, value)
    activePreparation.value = value
    if (
      value.status === 'cancel_requested' ||
      (preparationFinished(value) && value.status !== 'admitted')
    )
      pendingStops.delete(taskId)
    return value
  }
  async function followKnowledge(
    initial: KnowledgePreparation,
    taskId: string,
    parent: ContextTicket
  ): Promise<boolean> {
    if (initial.resource_type !== 'work_turn' || initial.resource_id !== taskId)
      throw new Error('知识检索准备与当前工作不匹配')
    const observation = observeScope.replace()
    const context: ContextTicket = {
      signal: AbortSignal.any([parent.signal, observation.signal]),
      isCurrent: () =>
        parent.isCurrent() && observation.isCurrent() && currentTask.value?.taskId === taskId
    }
    initial = updatePreparation(initial, taskId)
    if (currentTask.value) currentTask.value.preparationId = initial.id
    runError.value = ''
    disconnected.value = false
    try {
      if (pendingStops.has(taskId) && !preparationFinished(initial)) {
        await cancelRun(false)
        if (!context.isCurrent()) return false
        if (pendingStops.has(taskId))
          throw new Error(runError.value || '停止结果待确认，请恢复查看')
        initial = activePreparation.value ?? initial
      }
      const final = await observePreparation(
        initial,
        (value) => {
          if (!context.isCurrent()) return
          return updatePreparation(value, taskId)
        },
        context.signal
      )
      if (!context.isCurrent()) return false
      const priorIntent = intents.get(taskId)
      intents.delete(taskId)
      const task = await refreshTask(taskId, context)
      if (!task) return false
      if (final.run) {
        if (activeRun.value?.id !== final.run.id) activeRun.value = null
        applyRun(final.run, taskId)
        void follow(activeRun.value ?? final.run, taskId, parent)
        return true
      }
      if (task.preparationId || task.currentRunId)
        throw new Error('知识检索已结束，工作状态尚未确认，请恢复查看')
      retryKind.value = priorIntent ? 'failed' : ''
      if (priorIntent) intents.set(taskId, priorIntent)
      runError.value =
        final.error?.message ??
        (final.status === 'cancelled'
          ? '知识检索已取消，可重新发送'
          : '知识检索未完成，请检查知识库后重新发送')
      void loadTasks()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = report(reason)
        disconnected.value = true
      }
    }
    return false
  }
  function stopLocalActivity(): void {
    taskScope.replace()
    observeScope.replace()
    mutationScope.replace()
    createScope.replace()
    agentsScope.replace()
    admitting.value = false
    mutating.value = false
    stopping.value = false
    createLoading.value = false
    detailLoading.value = false
    agentsLoading.value = false
  }
  function clearCurrentTask(): void {
    stopLocalActivity()
    currentTask.value = null
    activeRun.value = null
    activePreparation.value = null
    runError.value = ''
    detailError.value = ''
    retryKind.value = ''
    disconnected.value = false
  }
  async function loadTask(taskId: string): Promise<void> {
    stopLocalActivity()
    const context = taskScope.current()
    currentTask.value = null
    activeRun.value = null
    activePreparation.value = null
    detailLoading.value = true
    detailError.value = ''
    runError.value = ''
    retryKind.value = ''
    disconnected.value = false
    try {
      const task = await api.getTask(taskId, context.signal)
      if (!context.isCurrent()) return
      currentTask.value = task
      recordTask(task)
      if (
        pendingCreate?.clientRequestId === task.createClientRequestId &&
        task.ownerId === identity.value?.id
      ) {
        pendingCreate = null
        createDraft.value = { goal: '', agentIds: [], activeAgentId: null }
      }
      detailLoading.value = false
      void loadAvailableAgents(taskId)
      if (task.preparationId) {
        const preparation = await getPreparation(task.preparationId, context.signal)
        if (!context.isCurrent()) return
        void followKnowledge(preparation, taskId, context)
      } else if (task.currentRunId) {
        const run = await api.getWorkRun(taskId, task.currentRunId, context.signal)
        if (!context.isCurrent()) return
        intents.delete(taskId)
        applyRun(run, taskId)
        void follow(run, taskId, context)
      } else {
        const intent = intents.get(taskId)
        const latestReply = [...task.messages]
          .reverse()
          .find((message) => message.role === 'assistant')
        if (
          intent &&
          taskLocks.has(taskId) &&
          !task.messages.some(
            (message) =>
              message.role === 'assistant' && message.clientRequestId === intent.clientRequestId
          )
        ) {
          retryKind.value = 'uncertain'
          runError.value = '上次请求结果待确认，请重试原请求'
        } else if (!latestReply && task.messages.length === 1 && task.activeAgentId) {
          const goal = task.messages[0]!
          intents.set(taskId, {
            taskId,
            agentId: task.activeAgentId,
            message: goal.content,
            expectedVersion: task.version,
            clientRequestId: task.createClientRequestId
          })
          retryKind.value = 'unstarted'
          runError.value = '目标已保存，尚未执行'
        } else if (latestReply && latestReply.runStatus !== 'completed') {
          if (intent) intents.delete(taskId)
          retryKind.value = 'failed'
          runError.value = latestReply.errorSnapshot?.message ?? '上轮未完成，可重试执行'
        } else if (intent) intents.delete(taskId)
        recordTask(task)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        if (
          currentTask.value?.taskId === taskId &&
          (currentTask.value.currentRunId || currentTask.value.preparationId)
        ) {
          runError.value = report(reason)
          disconnected.value = true
        } else detailError.value = report(reason)
      }
    } finally {
      if (context.isCurrent()) detailLoading.value = false
    }
  }
  async function createTask(draft: WorkDraft): Promise<WorkTask | null> {
    if (createLoading.value) return null
    const intent = nextCreateIntent(pendingCreate, draft, () => createRequestId())
    if (!intent.activeAgentId) return null
    pendingCreate = intent
    createDraft.value = {
      goal: intent.goal,
      agentIds: [...intent.agentIds],
      activeAgentId: intent.activeAgentId
    }
    const context = createScope.replace()
    createLoading.value = true
    runError.value = ''
    try {
      const task = await api.createTask(
        {
          goal: intent.goal,
          agent_ids: intent.agentIds,
          active_agent_id: intent.activeAgentId,
          client_request_id: intent.clientRequestId
        },
        context.signal
      )
      if (!context.isCurrent()) return null
      intents.set(task.taskId, {
        taskId: task.taskId,
        agentId: intent.activeAgentId,
        message: intent.goal,
        expectedVersion: task.version,
        clientRequestId: intent.clientRequestId
      })
      pendingCreate = null
      createDraft.value = { goal: '', agentIds: [], activeAgentId: null }
      void loadTasks()
      return task
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = report(reason)
        throw reason
      }
      return null
    } finally {
      if (context.isCurrent()) createLoading.value = false
    }
  }
  async function runMessage(
    input: { agentId: string; message: string },
    retry = false
  ): Promise<boolean> {
    const task = currentTask.value
    if (
      !task ||
      runLocked.value ||
      disconnected.value ||
      (!retry && taskLocks.has(task.taskId)) ||
      (!retry && retryKind.value === 'uncertain')
    )
      return false
    const previous = intents.get(task.taskId) ?? null
    const intent = retry
      ? previous
      : nextWorkIntent(
          previous,
          {
            taskId: task.taskId,
            agentId: input.agentId,
            message: input.message,
            expectedVersion: task.version
          },
          () => createRequestId()
        )
    if (!intent) return false
    intents.set(task.taskId, intent)
    taskLocks.set(task.taskId, task.version + 1)
    const context = taskScope.current()
    admitting.value = true
    runError.value = ''
    retryKind.value = ''
    try {
      const admission = await api.admitTurn(
        task.taskId,
        {
          agent_id: intent.agentId,
          client_request_id: intent.clientRequestId,
          message: intent.message,
          expected_version: intent.expectedVersion
        },
        context.signal
      )
      if (!context.isCurrent() || currentTask.value?.taskId !== task.taskId) return false
      if (isPreparation(admission)) {
        if (activeRun.value?.id !== admission.run?.id) activeRun.value = null
        currentTask.value.preparationId = admission.id
        activePreparation.value = admission
        admitting.value = false
        return await followKnowledge(admission, task.taskId, context)
      }
      const run = admission
      intents.delete(task.taskId)
      taskLocks.set(task.taskId, Math.max(task.version + 1, taskLocks.get(task.taskId) ?? 0))
      activeRun.value = null
      applyRun(run, task.taskId)
      // Acknowledged admission is never retried because a later refresh failed.
      await refreshTask(task.taskId, context)
      if (!context.isCurrent()) return false
      admitting.value = false
      void follow(run, task.taskId, context)
      return true
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = report(reason)
        if (intents.has(task.taskId)) {
          const definite = reason instanceof ApiError && reason.status < 500
          retryKind.value = definite ? '' : 'uncertain'
          if (definite) {
            intents.delete(task.taskId)
            taskLocks.delete(task.taskId)
            if (reason.status === 409) void loadTask(task.taskId)
          }
        } else disconnected.value = true
      }
      return false
    } finally {
      if (context.isCurrent()) admitting.value = false
    }
  }
  async function retryRun(): Promise<void> {
    const task = currentTask.value
    if (!task) return
    if (disconnected.value) {
      await reconnect()
      return
    }
    const pending = intents.get(task.taskId)
    if (pending) {
      if (retryKind.value === 'failed') {
        intents.delete(task.taskId)
        await runMessage({ agentId: pending.agentId, message: pending.message })
      } else await runMessage({ agentId: pending.agentId, message: pending.message }, true)
      return
    }
    const latest = [...task.messages].reverse().find((message) => message.role === 'assistant')
    const user =
      latest &&
      [...task.messages]
        .reverse()
        .find(
          (message) => message.role === 'user' && message.clientRequestId === latest.clientRequestId
        )
    if (latest?.agentId && user)
      await runMessage({ agentId: latest.agentId, message: user.content })
  }
  async function reconnect(): Promise<void> {
    const task = currentTask.value
    if (!task) return
    const context = taskScope.current()
    const operation = stopOperations.get(task.taskId)
    if (operation) await operation
    if (!context.isCurrent()) return
    try {
      const refreshed = await refreshTask(task.taskId, context)
      if (!refreshed) return
      if (refreshed.preparationId) {
        const preparation = await getPreparation(refreshed.preparationId, context.signal)
        if (context.isCurrent()) void followKnowledge(preparation, task.taskId, context)
        return
      }
      const runId = refreshed.currentRunId ?? activeRun.value?.id
      if (!runId) {
        disconnected.value = false
        return
      }
      const run = await api.getWorkRun(task.taskId, runId, context.signal)
      if (!context.isCurrent()) return
      applyRun(run, task.taskId)
      runError.value = ''
      void follow(run, task.taskId, context)
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) runError.value = report(reason)
    }
  }
  function cancelRun(observe = true): Promise<void> {
    const task = currentTask.value,
      runId = task?.currentRunId ?? activeRun.value?.id
    const preparationId =
      task?.preparationId ??
      (!runId && activePreparation.value && !preparationFinished(activePreparation.value)
        ? activePreparation.value.id
        : null)
    if (!task || (!runId && !preparationId) || admitting.value) return Promise.resolve()
    if (stopAcknowledged.value) return Promise.resolve()
    pendingStops.add(task.taskId)
    const existing = stopOperations.get(task.taskId)
    if (existing) return existing
    const context = taskScope.current()
    stopping.value = true
    const operation = (async () => {
      try {
        if (preparationId) {
          const value = await cancelPreparation(preparationId, context.signal)
          if (!context.isCurrent()) return
          updatePreparation(value, task.taskId)
          if (value.run) {
            const stopped =
              isTerminal(value.run) || value.run.status === 'cancel_requested'
                ? value.run
                : await api.cancelWorkRun(task.taskId, value.run.id, context.signal)
            if (!context.isCurrent()) return
            applyRun(stopped, task.taskId)
            if (observe) void follow(stopped, task.taskId, context)
          } else if (observe) void followKnowledge(value, task.taskId, context)
          return
        }
        if (!runId) return
        const run = await api.cancelWorkRun(task.taskId, runId, context.signal)
        if (!context.isCurrent()) return
        applyRun(run, task.taskId)
        runError.value = ''
        disconnected.value = false
        if (isTerminal(run)) await refreshTask(task.taskId, context)
        else if (observe) void follow(run, task.taskId, context)
      } catch (reason) {
        if (context.isCurrent() && !isAborted(reason)) {
          runError.value = report(reason)
          disconnected.value = true
        }
      } finally {
        if (context.isCurrent()) stopping.value = false
      }
    })()
    stopOperations.set(task.taskId, operation)
    void operation.then(() => {
      if (stopOperations.get(task.taskId) === operation) stopOperations.delete(task.taskId)
    })
    return operation
  }
  async function mutate(
    operation: (task: WorkTask, signal: AbortSignal) => Promise<WorkTask>
  ): Promise<boolean> {
    const task = currentTask.value
    if (!task || submissionBlocked.value) return false
    const context = mutationScope.replace(),
      parent = taskScope.current()
    mutating.value = true
    detailError.value = ''
    try {
      const result = await operation(task, context.signal)
      if (!context.isCurrent() || !parent.isCurrent() || currentTask.value?.taskId !== task.taskId)
        return false
      currentTask.value = result
      void loadTasks()
      return true
    } catch (reason) {
      if (context.isCurrent() && parent.isCurrent() && !isAborted(reason)) {
        detailError.value = report(reason)
        throw reason
      }
      return false
    } finally {
      if (context.isCurrent() && parent.isCurrent()) mutating.value = false
    }
  }
  const saveTask = (changes: { title?: string; goal?: string; status?: WorkStatus }) =>
    mutate((task, signal) =>
      api.updateTask(task.taskId, { expected_version: task.version, ...changes }, signal)
    )
  const addMember = (id: string) =>
    mutate((task, signal) => api.addMember(task.taskId, id, task.version, signal))
  const removeMember = (id: string) =>
    mutate((task, signal) => api.removeMember(task.taskId, id, task.version, signal))
  const upgradeMember = (member: WorkMember) =>
    mutate((task, signal) =>
      api.upgradeMember(
        task.taskId,
        member.agentId,
        task.version,
        member.latestPublishedVersion ?? member.publishedVersion,
        signal
      )
    )
  onBeforeUnmount(() => {
    for (const scope of [
      listScope,
      taskScope,
      agentsScope,
      mutationScope,
      createScope,
      observeScope
    ])
      scope.dispose()
    intents.clear()
    pendingStops.clear()
    stopOperations.clear()
    taskLocks.clear()
    pendingCreate = null
    tasks.value = []
    currentTask.value = null
    createDraft.value = { goal: '', agentIds: [], activeAgentId: null }
  })
  return reactive({
    tasks,
    total,
    filters,
    listLoading,
    listError,
    currentTask,
    detailLoading,
    detailError,
    availableAgents,
    agentsLoading,
    agentsError,
    createLoading,
    createDraft,
    admitting,
    mutating,
    stopping,
    stopAcknowledged,
    runLocked,
    canStop,
    isTaskBusy,
    submissionBlocked,
    runError,
    retryKind,
    disconnected,
    loadTasks,
    loadAvailableAgents,
    loadTask,
    clearCurrentTask,
    stopLocalActivity,
    createTask,
    runMessage,
    retryRun,
    reconnect,
    cancelRun,
    saveTask,
    addMember,
    removeMember,
    upgradeMember
  })
}
type WorkWorkspace = ReturnType<typeof createWorkWorkspace>
const workspaceKey: InjectionKey<WorkWorkspace> = Symbol('work-workspace')
export function provideWorkWorkspace(): void {
  provide(workspaceKey, createWorkWorkspace())
}
export function useWorkWorkspace(): WorkWorkspace {
  const state = inject(workspaceKey)
  if (!state) throw new Error('Work workspace provider is required')
  return state
}
