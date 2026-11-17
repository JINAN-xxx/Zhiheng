import { computed, reactive, ref } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import * as api from '../api/index.ts'
import type { WorkTask } from '../api/index.ts'
import type { WorkTasks } from './tasks.ts'

interface WorkRunObservationOptions {
  tasks: WorkTasks
  canObserve: (task: WorkTask) => boolean
  runSettled: (run: RunSnapshot) => void
}

export function useWorkRunObservation(options: WorkRunObservationOptions) {
  const { tasks } = options
  const activeRun = ref<RunSnapshot | null>(null)
  const runError = ref(''),
    disconnected = ref(false),
    stopping = ref(false)
  const pendingStops = reactive(new Set<string>())
  const stopOperations = new Map<string, { context: ContextTicket; promise: Promise<void> }>()
  const observeScope = new RequestScope()
  let refreshTimer: ReturnType<typeof setTimeout> | null = null
  const stopAcknowledged = computed(() => activeRun.value?.status === 'cancel_requested')
  const canStop = computed(() => {
    const task = tasks.currentTask.value
    return (
      !!task &&
      task.mode === 'agent' &&
      options.canObserve(task) &&
      (Boolean(task.currentRunId) || Boolean(activeRun.value && !isTerminal(activeRun.value)))
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
      run.resource_type !== 'work_agent' ||
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
      run.resource_type !== 'work_agent' ||
      run.resource_id !== taskId
    )
      throw new Error('运行与当前任务不匹配')
    if (activeRun.value?.id !== run.id) activeRun.value = null
    return applyRun(run, taskId)
  }
  function clearRefresh(): void {
    if (refreshTimer !== null) clearTimeout(refreshTimer)
    refreshTimer = null
  }
  function refreshProgress(taskId: string, context: ContextTicket): void {
    if (refreshTimer !== null || !context.isCurrent()) return
    refreshTimer = setTimeout(() => {
      refreshTimer = null
      if (context.isCurrent())
        void tasks.refreshTask(taskId, context).catch(() => {
          // The durable run stream remains authoritative; reconnect also reloads the task projection.
        })
    }, 400)
  }
  async function follow(run: RunSnapshot, taskId: string, parent: ContextTicket): Promise<void> {
    const current = tasks.currentTask.value
    if (
      !parent.isCurrent() ||
      !current ||
      current.taskId !== taskId ||
      current.mode !== 'agent' ||
      !options.canObserve(current)
    )
      return
    let context = observationContext(taskId, parent)
    clearRefresh()
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
          if (context.isCurrent() && applyRun(snapshot, taskId)) refreshProgress(taskId, context)
        },
        context.signal
      )
      if (!context.isCurrent()) return
      clearRefresh()
      const accepted = applyRun(terminal, taskId)
      if (!accepted || !isTerminal(accepted)) return
      context = observationContext(taskId, parent)
      const task = await tasks.refreshTask(taskId, context)
      if (!task) return
      if (task.currentRunId) throw new Error('运行已结束，任务状态尚未确认，请恢复查看')
      options.runSettled(accepted)
      runError.value =
        accepted.error?.message ??
        (accepted.status === 'completed' ? '' : '本轮已停止或中断，已确认的文件和进度已保留')
      void tasks.loadTasks()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        runError.value = errorMessage(reason)
        disconnected.value = true
      }
    }
  }
  function cancelRun(observe = true): Promise<void> {
    const task = tasks.currentTask.value
    if (!task || task.mode !== 'agent' || !options.canObserve(task) || stopAcknowledged.value)
      return Promise.resolve()
    const runId =
      task.currentRunId ??
      (activeRun.value && !isTerminal(activeRun.value) ? activeRun.value.id : null)
    if (!runId) return Promise.resolve()
    pendingStops.add(task.taskId)
    const existing = stopOperations.get(task.taskId)
    if (existing?.context.isCurrent()) return existing.promise
    const context = tasks.taskScope.current()
    stopping.value = true
    const operation = (async () => {
      try {
        const incoming = await api.cancelWorkRun(task.taskId, runId, context.signal)
        if (!context.isCurrent()) return
        const run = applyRun(incoming, task.taskId)
        if (!run) return
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
      task.mode !== 'agent' ||
      !options.canObserve(task)
    )
      return
    try {
      if (task.currentRunId) {
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
    if (!task || task.mode !== 'agent' || !options.canObserve(task)) return
    const parent = tasks.taskScope.current()
    const operation = stopOperations.get(task.taskId)
    if (operation?.context.isCurrent()) await operation.promise
    if (!parent.isCurrent()) return
    const context = observationContext(task.taskId, parent)
    try {
      const refreshed = await tasks.refreshTask(task.taskId, context)
      if (!refreshed) return
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
    clearRefresh()
    observeScope.replace()
    activeRun.value = null
    runError.value = ''
    disconnected.value = false
    stopping.value = false
  }
  function dispose(): void {
    clearRefresh()
    observeScope.dispose()
    pendingStops.clear()
    stopOperations.clear()
  }
  return {
    activeRun,
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
    restore,
    reconnect,
    cancelRun,
    reset,
    dispose
  }
}
export type WorkRunObservation = ReturnType<typeof useWorkRunObservation>
