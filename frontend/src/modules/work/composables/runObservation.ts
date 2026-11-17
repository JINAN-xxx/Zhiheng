import { computed, reactive, ref } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import {
  cancelPreparation,
  getPreparation,
  observePreparation,
  projectPreparation,
  preparationFinished
} from '../../knowledge/public.ts'
import type { KnowledgePreparation } from '../../knowledge/public.ts'
import * as api from '../api/index.ts'
import type { WorkTask } from '../api/index.ts'
import type { WorkTasks } from './tasks.ts'

interface WorkRunObservationOptions {
  tasks: WorkTasks
  canObserve: (task: WorkTask) => boolean
  runSettled: (run: RunSnapshot) => void
  preparationSettled: (preparation: KnowledgePreparation, taskId: string) => void
}

export function useWorkRunObservation(options: WorkRunObservationOptions) {
  const { tasks } = options
  const activeRun = ref<RunSnapshot | null>(null)
  const activePreparation = ref<KnowledgePreparation | null>(null)
  const runError = ref(''),
    disconnected = ref(false),
    stopping = ref(false)
  const pendingStops = reactive(new Set<string>())
  const stopOperations = new Map<string, { context: ContextTicket; promise: Promise<void> }>()
  const observeScope = new RequestScope()
  const stopAcknowledged = computed(() => {
    const preparation = activePreparation.value
    return preparation && !preparationFinished(preparation)
      ? preparation.status === 'cancel_requested'
      : activeRun.value?.status === 'cancel_requested'
  })
  const canStop = computed(() => {
    const task = tasks.currentTask.value
    if (!task || !options.canObserve(task)) return false
    return (
      Boolean(task.currentRunId) ||
      Boolean(task.preparationId) ||
      Boolean(activePreparation.value && !preparationFinished(activePreparation.value)) ||
      Boolean(activeRun.value && !isTerminal(activeRun.value))
    )
  })
  const occupied = computed(
    () =>
      canStop.value ||
      Boolean(tasks.currentTask.value && pendingStops.has(tasks.currentTask.value.taskId))
  )

  function observationContext(taskId: string, parent: ContextTicket): ContextTicket {
    const observation = observeScope.replace()
    return {
      signal: AbortSignal.any([parent.signal, observation.signal]),
      isCurrent: () =>
        parent.isCurrent() && observation.isCurrent() && tasks.currentTask.value?.taskId === taskId
    }
  }
  function projectCurrentRun(): void {
    const task = tasks.currentTask.value,
      run = activeRun.value
    if (!task || !run || run.resource_id !== task.taskId) return
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
  function applyRun(run: RunSnapshot, taskId: string): RunSnapshot | null {
    if (
      tasks.currentTask.value?.taskId !== taskId ||
      run.resource_type !== 'work_turn' ||
      run.resource_id !== taskId
    )
      return null
    if (acceptSnapshot(activeRun.value, run)) {
      activeRun.value = run
      if (isTerminal(run) || run.status === 'cancel_requested') pendingStops.delete(taskId)
      projectCurrentRun()
    }
    return activeRun.value?.id === run.id ? activeRun.value : null
  }
  function adoptRun(run: RunSnapshot, taskId: string): RunSnapshot | null {
    if (
      tasks.currentTask.value?.taskId !== taskId ||
      run.resource_type !== 'work_turn' ||
      run.resource_id !== taskId
    )
      throw new Error('运行与当前工作不匹配')
    if (activeRun.value?.id !== run.id) activeRun.value = null
    const accepted = applyRun(run, taskId)
    if (accepted) activePreparation.value = null
    return accepted
  }
  function updatePreparation(value: KnowledgePreparation, taskId: string): KnowledgePreparation {
    if (
      tasks.currentTask.value?.taskId !== taskId ||
      value.resource_type !== 'work_turn' ||
      value.resource_id !== taskId
    )
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
  async function follow(run: RunSnapshot, taskId: string, parent: ContextTicket): Promise<void> {
    const current = tasks.currentTask.value
    if (
      !parent.isCurrent() ||
      !current ||
      current.taskId !== taskId ||
      !options.canObserve(current)
    )
      return
    const context = observationContext(taskId, parent)
    disconnected.value = false
    try {
      run = applyRun(run, taskId) ?? run
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
      const accepted = applyRun(terminal, taskId)
      if (!accepted || !isTerminal(accepted)) return
      const task = await tasks.refreshTask(taskId, context)
      if (!task) return
      if (task.currentRunId || task.preparationId)
        throw new Error('运行已结束，任务状态尚未确认，请恢复查看')
      options.runSettled(accepted)
      runError.value =
        accepted.error?.message ??
        (accepted.status === 'completed' ? '' : '本轮已停止或中断，可用新请求重试')
      void tasks.loadTasks()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = errorMessage(reason)
        disconnected.value = true
      }
    }
  }
  async function followPreparation(
    initial: KnowledgePreparation,
    taskId: string,
    parent: ContextTicket
  ): Promise<boolean> {
    const current = tasks.currentTask.value
    if (
      !parent.isCurrent() ||
      !current ||
      current.taskId !== taskId ||
      !options.canObserve(current)
    )
      return false
    const context = observationContext(taskId, parent)
    initial = updatePreparation(initial, taskId)
    current.preparationId = initial.id
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
          if (context.isCurrent()) return updatePreparation(value, taskId)
        },
        context.signal
      )
      if (!context.isCurrent()) return false
      const task = await tasks.refreshTask(taskId, context)
      if (!task) return false
      options.preparationSettled(final, taskId)
      if (final.run) {
        const run = adoptRun(final.run, taskId)
        if (run) void follow(run, taskId, parent)
        return true
      }
      if (task.preparationId || task.currentRunId)
        throw new Error('知识检索已结束，工作状态尚未确认，请恢复查看')
      runError.value =
        final.error?.message ??
        (final.status === 'cancelled'
          ? '知识检索已取消，可重新发送'
          : '知识检索未完成，请检查知识库后重新发送')
      void tasks.loadTasks()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = errorMessage(reason)
        disconnected.value = true
      }
    }
    return false
  }

  function cancelRun(observe = true): Promise<void> {
    const task = tasks.currentTask.value
    if (!task || !options.canObserve(task) || stopAcknowledged.value) return Promise.resolve()
    const preparationId =
      task.preparationId ??
      (activePreparation.value && !preparationFinished(activePreparation.value)
        ? activePreparation.value.id
        : null)
    const runId =
      task.currentRunId ??
      (activeRun.value && !isTerminal(activeRun.value) ? activeRun.value.id : null)
    if (!preparationId && !runId) return Promise.resolve()
    pendingStops.add(task.taskId)
    const existing = stopOperations.get(task.taskId)
    if (existing?.context.isCurrent()) return existing.promise
    const context = tasks.taskScope.current()
    stopping.value = true
    const operation = (async () => {
      try {
        let run: RunSnapshot | null = null
        if (preparationId) {
          const incoming = await cancelPreparation(preparationId, context.signal)
          if (!context.isCurrent()) return
          const value = updatePreparation(incoming, task.taskId)
          if (!value.run) {
            if (observe) void followPreparation(value, task.taskId, context)
            return
          }
          run = adoptRun(value.run, task.taskId)
          if (run && !isTerminal(run) && run.status !== 'cancel_requested') {
            const stopped = await api.cancelWorkRun(task.taskId, run.id, context.signal)
            if (!context.isCurrent()) return
            run = applyRun(stopped, task.taskId)
          }
        } else if (runId) {
          const incoming = await api.cancelWorkRun(task.taskId, runId, context.signal)
          if (!context.isCurrent()) return
          run = applyRun(incoming, task.taskId)
        }
        if (!context.isCurrent() || !run) return
        runError.value = ''
        disconnected.value = false
        if (observe) {
          if (isTerminal(run)) await follow(run, task.taskId, context)
          else void follow(run, task.taskId, context)
        }
      } catch (reason) {
        if (context.isCurrent() && !isAborted(reason)) {
          runError.value = errorMessage(reason)
          disconnected.value = true
        }
      } finally {
        if (context.isCurrent()) stopping.value = false
      }
    })()
    stopOperations.set(task.taskId, { context, promise: operation })
    void operation.then(() => {
      if (stopOperations.get(task.taskId)?.promise === operation) stopOperations.delete(task.taskId)
    })
    return operation
  }

  async function restore(task: WorkTask, context: ContextTicket): Promise<void> {
    if (
      !context.isCurrent() ||
      tasks.currentTask.value?.taskId !== task.taskId ||
      !options.canObserve(task)
    )
      return
    try {
      if (task.preparationId) {
        const preparation = await getPreparation(task.preparationId, context.signal)
        if (context.isCurrent()) void followPreparation(preparation, task.taskId, context)
      } else if (task.currentRunId) {
        const run = await api.getWorkRun(task.taskId, task.currentRunId, context.signal)
        if (!context.isCurrent()) return
        const accepted = adoptRun(run, task.taskId)
        if (accepted) void follow(accepted, task.taskId, context)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = errorMessage(reason)
        disconnected.value = true
      }
    }
  }
  async function reconnect(): Promise<void> {
    const task = tasks.currentTask.value
    if (!task || !options.canObserve(task)) return
    const parent = tasks.taskScope.current()
    const operation = stopOperations.get(task.taskId)
    if (operation?.context.isCurrent()) await operation.promise
    if (!parent.isCurrent()) return
    const context = observationContext(task.taskId, parent)
    try {
      const refreshed = await tasks.refreshTask(task.taskId, context)
      if (!refreshed) return
      if (refreshed.preparationId) {
        const preparation = await getPreparation(refreshed.preparationId, context.signal)
        if (context.isCurrent()) void followPreparation(preparation, task.taskId, parent)
        return
      }
      const runId = refreshed.currentRunId ?? activeRun.value?.id
      if (!runId) {
        disconnected.value = false
        runError.value = ''
        return
      }
      const incoming = await api.getWorkRun(task.taskId, runId, context.signal)
      if (!context.isCurrent()) return
      const run = adoptRun(incoming, task.taskId)
      runError.value = ''
      if (run) void follow(run, task.taskId, parent)
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = errorMessage(reason)
        disconnected.value = true
      }
    }
  }
  function reset(): void {
    observeScope.replace()
    activeRun.value = null
    activePreparation.value = null
    runError.value = ''
    disconnected.value = false
    stopping.value = false
  }
  function dispose(): void {
    observeScope.dispose()
    pendingStops.clear()
    stopOperations.clear()
  }
  return {
    activeRun,
    activePreparation,
    runError,
    disconnected,
    stopping,
    pendingStops,
    stopAcknowledged,
    canStop,
    occupied,
    projectCurrentRun,
    adoptRun,
    follow,
    followPreparation,
    restore,
    reconnect,
    cancelRun,
    reset,
    dispose
  }
}
export type WorkRunObservation = ReturnType<typeof useWorkRunObservation>
