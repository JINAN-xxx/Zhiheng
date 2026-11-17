import { computed, reactive, ref } from 'vue'
import { createRequestId } from '../../../shared/requestId.ts'
import { errorMessage, isAborted, isDefiniteRejection, RequestScope } from '../../../shared/http.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { isPreparation } from '../../knowledge/public.ts'
import type { KnowledgePreparation } from '../../knowledge/public.ts'
import * as api from '../api/index.ts'
import type { WorkMember, WorkStatus, WorkSummary, WorkTask } from '../api/index.ts'
import { nextCreateIntent, nextWorkIntent } from '../presentation.ts'
import type { CreateIntent, WorkDraft, WorkIntent } from '../presentation.ts'
import type { WorkTasks } from './tasks.ts'
import type { WorkRunObservation } from './runObservation.ts'

interface WorkTurnCommandsOptions {
  tasks: WorkTasks
  observation: WorkRunObservation
  canCreate: () => boolean
  canEdit: (task: WorkTask) => boolean
  canRun: (task: WorkTask) => boolean
  ownerId: () => string | undefined
  reloadTask: (taskId: string) => Promise<void>
}

export function useWorkTurnCommands(options: WorkTurnCommandsOptions) {
  const { tasks, observation } = options
  const createLoading = ref(false),
    admitting = ref(false),
    mutating = ref(false)
  const retryKind = ref<'' | 'unstarted' | 'uncertain' | 'failed'>('')
  const createDraft = ref<WorkDraft>({ goal: '', agentIds: [], activeAgentId: null })
  const mutationScope = new RequestScope(),
    createScope = new RequestScope()
  const intents = new Map<string, WorkIntent>()
  const taskLocks = reactive(new Map<string, number>())
  let pendingCreate: CreateIntent | null = null
  const runLocked = computed(
    () =>
      admitting.value || mutating.value || observation.stopping.value || observation.occupied.value
  )
  const submissionBlocked = computed(
    () =>
      runLocked.value ||
      retryKind.value === 'uncertain' ||
      observation.disconnected.value ||
      Boolean(tasks.currentTask.value && taskLocks.has(tasks.currentTask.value.taskId))
  )

  function recordTask(task: WorkSummary): void {
    const lock = taskLocks.get(task.taskId)
    if (lock !== undefined && task.version < lock) return
    if (task.currentRunId || task.preparationId) taskLocks.set(task.taskId, task.version)
    else if (!intents.has(task.taskId)) {
      taskLocks.delete(task.taskId)
      observation.pendingStops.delete(task.taskId)
    }
  }
  function isTaskBusy(taskId: string): boolean {
    return (
      taskLocks.has(taskId) ||
      (tasks.currentTask.value?.taskId === taskId && submissionBlocked.value)
    )
  }
  function runSettled(run: RunSnapshot): void {
    retryKind.value = run.status === 'completed' ? '' : 'failed'
  }
  function preparationSettled(preparation: KnowledgePreparation, taskId: string): void {
    if (preparation.run) {
      intents.delete(taskId)
      retryKind.value = ''
    } else {
      retryKind.value = intents.has(taskId) ? 'failed' : ''
      taskLocks.delete(taskId)
    }
  }
  function restoreTask(task: WorkTask): void {
    const taskId = task.taskId
    if (
      pendingCreate?.clientRequestId === task.createClientRequestId &&
      task.ownerId === options.ownerId()
    ) {
      pendingCreate = null
      createDraft.value = { goal: '', agentIds: [], activeAgentId: null }
    }
    if (task.currentRunId) {
      intents.delete(taskId)
      return
    }
    if (task.preparationId) return
    const intent = intents.get(taskId)
    const latestReply = [...task.messages].reverse().find((message) => message.role === 'assistant')
    if (
      intent &&
      taskLocks.has(taskId) &&
      !task.messages.some(
        (message) =>
          message.role === 'assistant' && message.clientRequestId === intent.clientRequestId
      )
    ) {
      retryKind.value = 'uncertain'
      observation.runError.value = '上次请求结果待确认，请重试原请求'
    } else if (!latestReply && task.messages.length === 1 && task.activeAgentId) {
      intents.set(taskId, {
        taskId,
        agentId: task.activeAgentId,
        message: task.messages[0]!.content,
        expectedVersion: task.version,
        clientRequestId: task.createClientRequestId
      })
      retryKind.value = 'unstarted'
      observation.runError.value = '目标已保存，尚未执行'
    } else if (latestReply && latestReply.runStatus !== 'completed') {
      if (intent) intents.delete(taskId)
      retryKind.value = 'failed'
      observation.runError.value = latestReply.errorSnapshot?.message ?? '上轮未完成，可重试执行'
    } else if (intent) intents.delete(taskId)
    recordTask(task)
  }

  async function createTask(draft: WorkDraft): Promise<WorkTask | null> {
    if (createLoading.value || !options.canCreate()) return null
    const intent = nextCreateIntent(pendingCreate, draft, createRequestId)
    if (!intent.activeAgentId) return null
    pendingCreate = intent
    createDraft.value = {
      goal: intent.goal,
      agentIds: [...intent.agentIds],
      activeAgentId: intent.activeAgentId
    }
    const context = createScope.replace()
    createLoading.value = true
    observation.runError.value = ''
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
      void tasks.loadTasks()
      return task
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        observation.runError.value = errorMessage(reason)
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
    const task = tasks.currentTask.value
    if (
      !task ||
      !options.canRun(task) ||
      task.status !== 'active' ||
      runLocked.value ||
      observation.disconnected.value ||
      (!retry && (taskLocks.has(task.taskId) || retryKind.value === 'uncertain'))
    )
      return false
    const previous = intents.get(task.taskId) ?? null
    const confirming = retry && retryKind.value === 'uncertain'
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
          createRequestId
        )
    if (!intent) return false
    intents.set(task.taskId, intent)
    taskLocks.set(task.taskId, task.version + 1)
    const context = tasks.taskScope.current()
    admitting.value = true
    observation.runError.value = ''
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
      if (!context.isCurrent() || tasks.currentTask.value?.taskId !== task.taskId) return false
      if (isPreparation(admission)) {
        admitting.value = false
        return await observation.followPreparation(admission, task.taskId, context)
      }
      intents.delete(task.taskId)
      taskLocks.set(task.taskId, Math.max(task.version + 1, taskLocks.get(task.taskId) ?? 0))
      const run = observation.adoptRun(admission, task.taskId)
      await tasks.refreshTask(task.taskId, context)
      if (!context.isCurrent()) return false
      admitting.value = false
      if (run) void observation.follow(run, task.taskId, context)
      return true
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        observation.runError.value = errorMessage(reason)
        if (intents.has(task.taskId)) {
          const definite = !confirming && isDefiniteRejection(reason)
          retryKind.value = definite ? '' : 'uncertain'
          if (definite) {
            intents.delete(task.taskId)
            taskLocks.delete(task.taskId)
            if (reason.status === 409) void options.reloadTask(task.taskId)
          }
        } else observation.disconnected.value = true
      }
      return false
    } finally {
      if (context.isCurrent()) admitting.value = false
    }
  }
  async function retryRun(): Promise<void> {
    const task = tasks.currentTask.value
    if (!task) return
    if (observation.disconnected.value) {
      await observation.reconnect()
      return
    }
    if (!options.canRun(task)) return
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

  async function mutate(
    operation: (task: WorkTask, signal: AbortSignal) => Promise<WorkTask>
  ): Promise<boolean> {
    const task = tasks.currentTask.value
    if (!task || !options.canEdit(task) || submissionBlocked.value) return false
    const context = mutationScope.replace(),
      parent = tasks.taskScope.current()
    mutating.value = true
    tasks.detailError.value = ''
    try {
      const result = await operation(task, AbortSignal.any([context.signal, parent.signal]))
      if (!context.isCurrent() || !parent.isCurrent()) return false
      if (!tasks.acceptTask(result, parent)) return false
      void tasks.loadTasks()
      return true
    } catch (reason) {
      if (context.isCurrent() && parent.isCurrent() && !isAborted(reason)) {
        tasks.detailError.value = errorMessage(reason)
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
  async function addMembers(ids: string[]): Promise<void> {
    const taskId = tasks.currentTask.value?.taskId,
      parent = tasks.taskScope.current()
    if (!taskId) return
    for (const id of ids) {
      if (
        !parent.isCurrent() ||
        tasks.currentTask.value?.taskId !== taskId ||
        !(await addMember(id))
      )
        return
    }
  }
  const removeMember = (id: string) =>
    mutate((task, signal) => api.removeMember(task.taskId, id, task.version, signal))
  const upgradeMember = (member: WorkMember) =>
    mutate((task, signal) =>
      api.upgradeMember(
        task.taskId,
        member.agentId,
        task.version,
        member.latestAgentRevision ?? member.agentRevision,
        signal
      )
    )

  function stopLocalActivity(): void {
    mutationScope.replace()
    createScope.replace()
    admitting.value = false
    mutating.value = false
    createLoading.value = false
    retryKind.value = ''
  }
  function clear(): void {
    stopLocalActivity()
    intents.clear()
    taskLocks.clear()
    pendingCreate = null
    createDraft.value = { goal: '', agentIds: [], activeAgentId: null }
  }
  function dispose(): void {
    mutationScope.dispose()
    createScope.dispose()
    intents.clear()
    taskLocks.clear()
    pendingCreate = null
  }
  return {
    createLoading,
    createDraft,
    admitting,
    mutating,
    retryKind,
    runLocked,
    submissionBlocked,
    recordTask,
    isTaskBusy,
    runSettled,
    preparationSettled,
    restoreTask,
    createTask,
    runMessage,
    retryRun,
    saveTask,
    addMember,
    addMembers,
    removeMember,
    upgradeMember,
    stopLocalActivity,
    clear,
    dispose
  }
}
export type WorkTurnCommands = ReturnType<typeof useWorkTurnCommands>
