import { computed, reactive, ref } from 'vue'
import { createRequestId } from '../../../shared/requestId.ts'
import { errorMessage, isAborted, isDefiniteRejection, RequestScope } from '../../../shared/http.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import * as api from '../api/index.ts'
import type { UpdateWorkBody, WorkSummary, WorkTask } from '../api/index.ts'
import {
  emptyWorkDraft,
  MAX_WORK_FILE_BYTES,
  nextCreateIntent,
  nextWorkIntent
} from '../presentation.ts'
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
export interface WorkTurnInput {
  message: string
  fileIds?: string[]
  resume?: boolean
}

export function useWorkTurnCommands(options: WorkTurnCommandsOptions) {
  const { tasks, observation } = options
  const createLoading = ref(false),
    admitting = ref(false),
    mutating = ref(false),
    uploading = ref(false),
    createUncertain = ref(false)
  const retryKind = ref<'' | 'unstarted' | 'uncertain' | 'failed'>('')
  const createDraft = ref<WorkDraft>(emptyWorkDraft())
  const mutationScope = new RequestScope(),
    createScope = new RequestScope()
  const intents = new Map<string, WorkIntent>()
  const taskLocks = reactive(new Map<string, number>())
  let pendingCreate: CreateIntent | null = null
  const runLocked = computed(
    () =>
      admitting.value ||
      mutating.value ||
      uploading.value ||
      observation.stopping.value ||
      observation.occupied.value
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
    if (task.currentRunId) taskLocks.set(task.taskId, task.version)
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
  function restoreTask(task: WorkTask): void {
    const taskId = task.taskId
    if (
      pendingCreate?.clientRequestId === task.createClientRequestId &&
      task.ownerId === options.ownerId()
    ) {
      pendingCreate = null
      createUncertain.value = false
      createDraft.value = emptyWorkDraft()
    }
    if (task.mode !== 'agent') return
    if (task.currentRunId) {
      intents.delete(taskId)
      return
    }
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
      observation.runError.value = '上次请求结果待确认，请确认原请求'
    } else if (!latestReply && task.goal.trim() && task.messages.length <= 1) {
      intents.set(taskId, {
        taskId,
        message: task.goal,
        expectedVersion: task.version,
        clientRequestId: task.createClientRequestId,
        fileIds: [],
        resume: false
      })
      retryKind.value = 'unstarted'
      observation.runError.value = '目标已保存，尚未执行'
    } else if (latestReply && latestReply.runStatus !== 'completed') {
      if (intent) intents.delete(taskId)
      retryKind.value = 'failed'
      observation.runError.value =
        latestReply.errorSnapshot?.message ?? '上轮未完成，已确认的进度与成果已保留'
    } else if (intent) intents.delete(taskId)
    recordTask(task)
  }
  async function createTask(draft: WorkDraft): Promise<WorkTask | null> {
    if (createLoading.value || !options.canCreate()) return null
    const confirming = createUncertain.value
    const intent =
      confirming && pendingCreate
        ? pendingCreate
        : nextCreateIntent(pendingCreate, draft, createRequestId)
    if (!intent.modelId) return null
    pendingCreate = intent
    createDraft.value = {
      goal: intent.goal,
      modelId: intent.modelId,
      skillSlugs: [...intent.skillSlugs]
    }
    const context = createScope.replace()
    createLoading.value = true
    observation.runError.value = ''
    try {
      const task = await api.createTask(
        {
          goal: intent.goal,
          model_id: intent.modelId,
          skill_slugs: intent.skillSlugs,
          file_ids: [],
          client_request_id: intent.clientRequestId
        },
        context.signal
      )
      if (!context.isCurrent()) return null
      if (intent.goal.trim())
        intents.set(task.taskId, {
          taskId: task.taskId,
          message: intent.goal,
          expectedVersion: task.version,
          clientRequestId: intent.clientRequestId,
          fileIds: [],
          resume: false
        })
      pendingCreate = null
      createUncertain.value = false
      createDraft.value = emptyWorkDraft()
      void tasks.loadTasks()
      return task
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        createUncertain.value = confirming || !isDefiniteRejection(reason)
        if (!createUncertain.value) pendingCreate = null
        observation.runError.value = createUncertain.value
          ? '任务创建结果待确认。再次操作会沿用原请求。'
          : errorMessage(reason)
        throw reason
      }
      return null
    } finally {
      if (context.isCurrent()) createLoading.value = false
    }
  }
  function showPending(task: WorkTask, intent: WorkIntent): void {
    const now = new Date().toISOString()
    const base = {
      clientRequestId: intent.clientRequestId,
      reasoningContent: '',
      runId: null,
      runStatus: 'pending' as const,
      usage: null,
      errorSnapshot: null,
      agentId: null,
      agentNameSnapshot: null,
      agentRevision: null,
      createdAt: now,
      knowledgeSources: []
    }
    if (
      !intent.resume &&
      !task.messages.some(
        (message) => message.clientRequestId === intent.clientRequestId && message.role === 'user'
      )
    )
      task.messages.push({
        ...base,
        messageId: 'local:user:' + intent.clientRequestId,
        role: 'user',
        content: intent.message,
        sequence: task.messages.length + 1
      })
    if (
      !task.messages.some(
        (message) =>
          message.clientRequestId === intent.clientRequestId && message.role === 'assistant'
      )
    )
      task.messages.push({
        ...base,
        messageId: 'local:assistant:' + intent.clientRequestId,
        role: 'assistant',
        content: '',
        sequence: task.messages.length + 1
      })
  }
  function discardPending(task: WorkTask, intent: WorkIntent): void {
    task.messages = task.messages.filter(
      (message) =>
        !message.messageId.startsWith('local:') ||
        message.clientRequestId !== intent.clientRequestId
    )
  }
  async function runMessage(input: WorkTurnInput, retry = false): Promise<boolean> {
    const task = tasks.currentTask.value
    if (
      !task ||
      task.mode !== 'agent' ||
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
            message: input.message,
            expectedVersion: task.version,
            fileIds: input.fileIds ?? [],
            resume: input.resume ?? false
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
    showPending(task, intent)
    try {
      const admission = await api.admitTurn(
        task.taskId,
        {
          client_request_id: intent.clientRequestId,
          message: intent.message,
          expected_version: intent.expectedVersion,
          file_ids: intent.fileIds,
          resume: intent.resume
        },
        context.signal
      )
      if (!context.isCurrent() || tasks.currentTask.value?.taskId !== task.taskId) return false
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
            discardPending(task, intent)
            if (reason.status === 409) void options.reloadTask(task.taskId)
          } else {
            const pending = task.messages.find(
              (message) => message.messageId === 'local:assistant:' + intent.clientRequestId
            )
            if (pending)
              Object.assign(pending, {
                runStatus: 'interrupted',
                errorSnapshot: {
                  code: 'admission_unconfirmed',
                  message: '请求结果待确认，请确认原请求'
                }
              })
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
    if (!task || task.mode !== 'agent') return
    if (observation.disconnected.value) {
      await observation.reconnect()
      return
    }
    if (!options.canRun(task)) return
    const pending = intents.get(task.taskId)
    if (pending) {
      await runMessage(
        { message: pending.message, fileIds: pending.fileIds, resume: pending.resume },
        true
      )
      return
    }
    const latest = [...task.messages].reverse().find((message) => message.role === 'assistant')
    if (
      task.executionState === 'paused' ||
      (latest && ['failed', 'interrupted', 'cancelled'].includes(latest.runStatus))
    )
      await runMessage({ message: '', resume: true })
  }
  async function mutate(
    operation: (task: WorkTask, signal: AbortSignal) => Promise<WorkTask>
  ): Promise<boolean> {
    const task = tasks.currentTask.value
    if (!task || task.mode !== 'agent' || !options.canEdit(task) || submissionBlocked.value)
      return false
    const context = mutationScope.replace(),
      parent = tasks.taskScope.current()
    mutating.value = true
    tasks.detailError.value = ''
    try {
      const result = await operation(task, AbortSignal.any([context.signal, parent.signal]))
      if (!context.isCurrent() || !parent.isCurrent() || !tasks.acceptTask(result, parent))
        return false
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
  const saveTask = (changes: Omit<UpdateWorkBody, 'expected_version'>) =>
    mutate((task, signal) =>
      api.updateTask(task.taskId, { expected_version: task.version, ...changes }, signal)
    )
  async function uploadFiles(files: File[]): Promise<void> {
    const task = tasks.currentTask.value
    if (!task || task.mode !== 'agent' || !options.canRun(task) || submissionBlocked.value) return
    if (files.some((file) => file.size < 1 || file.size > MAX_WORK_FILE_BYTES))
      throw new Error('请选择非空文件，单个文件不超过 20 MiB')
    const parent = tasks.taskScope.current(),
      context = mutationScope.replace()
    uploading.value = true
    tasks.detailError.value = ''
    try {
      for (const file of files) {
        const uploaded = await api.uploadTaskFile(
          task.taskId,
          file,
          AbortSignal.any([parent.signal, context.signal])
        )
        if (!parent.isCurrent() || !context.isCurrent()) return
        const refreshed = tasks.acceptTask(uploaded, parent)
        if (!refreshed) return
        const unstarted = intents.get(task.taskId)
        if (unstarted && retryKind.value === 'unstarted')
          intents.set(task.taskId, { ...unstarted, expectedVersion: refreshed.version })
      }
      void tasks.loadTasks()
    } catch (reason) {
      if (parent.isCurrent() && context.isCurrent() && !isAborted(reason)) {
        tasks.detailError.value = isDefiniteRejection(reason)
          ? errorMessage(reason)
          : '上传结果尚未确认，请刷新资料列表后核对'
        void tasks.refreshTask(task.taskId, parent).catch(() => {})
        throw reason
      }
    } finally {
      if (parent.isCurrent() && context.isCurrent()) uploading.value = false
    }
  }
  function stopLocalActivity(): void {
    mutationScope.replace()
    createScope.replace()
    admitting.value = false
    mutating.value = false
    uploading.value = false
    createLoading.value = false
    retryKind.value = ''
  }
  function clear(): void {
    stopLocalActivity()
    intents.clear()
    taskLocks.clear()
    pendingCreate = null
    createUncertain.value = false
    createDraft.value = emptyWorkDraft()
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
    createUncertain,
    createDraft,
    admitting,
    mutating,
    uploading,
    retryKind,
    runLocked,
    submissionBlocked,
    recordTask,
    isTaskBusy,
    runSettled,
    restoreTask,
    createTask,
    runMessage,
    retryRun,
    saveTask,
    uploadFiles,
    stopLocalActivity,
    clear,
    dispose
  }
}
export type WorkTurnCommands = ReturnType<typeof useWorkTurnCommands>
