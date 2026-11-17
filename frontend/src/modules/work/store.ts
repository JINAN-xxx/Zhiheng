import { inject, onBeforeUnmount, provide, reactive, watch } from 'vue'
import type { InjectionKey } from 'vue'
import { canUseCapability, canUseModule, identity } from '../identity/public.ts'
import type { WorkTask } from './api/index.ts'
import { useWorkTasks } from './composables/tasks.ts'
import { useWorkRunObservation } from './composables/runObservation.ts'
import { useWorkTurnCommands } from './composables/turns.ts'
import type { WorkTurnCommands } from './composables/turns.ts'
import type { WorkRunObservation } from './composables/runObservation.ts'

function createWorkWorkspace() {
  const ownsTask = (task: WorkTask) => task.ownerId === identity.value?.id
  const canRun = (task: WorkTask) => canUseCapability('work', 'use') && ownsTask(task)
  const canObserve = (task: WorkTask) => canUseCapability('work', 'read') && ownsTask(task)
  // These callbacks run only from explicit resource/turn operations after composition.
  const tasks = useWorkTasks({
    canRead: () => canUseModule('work'),
    recordTask: (task) => commands.recordTask(task),
    projectCurrentRun: () => observation.projectCurrentRun()
  })
  const observation: WorkRunObservation = useWorkRunObservation({
    tasks,
    canObserve: (task) => canObserve(task) && !commands.admitting.value,
    runSettled: (run) => commands.runSettled(run),
    preparationSettled: (preparation, taskId) => commands.preparationSettled(preparation, taskId)
  })
  const commands: WorkTurnCommands = useWorkTurnCommands({
    tasks,
    observation,
    canCreate: () => canUseCapability('work', 'create'),
    canEdit: (task) => canUseCapability('work', 'update') && ownsTask(task),
    canRun,
    ownerId: () => identity.value?.id,
    reloadTask: loadTask
  })
  let locallyActive = false
  function stopLocalActivity(): void {
    locallyActive = false
    tasks.stopLocalActivity()
    commands.stopLocalActivity()
    observation.reset()
  }
  function clearCurrentTask(): void {
    stopLocalActivity()
    tasks.clearCurrentTask()
  }
  async function loadTask(taskId: string): Promise<void> {
    stopLocalActivity()
    locallyActive = true
    const task = await tasks.loadTask(taskId)
    if (!task) return
    const context = tasks.taskScope.current()
    commands.restoreTask(task)
    void tasks.loadAvailableAgents(taskId)
    await observation.restore(task, context)
  }

  // A permission refresh keeps this provider mounted. End the old ownership tickets
  // before any queued response or stream callback can project protected state.
  watch(
    [
      () => identity.value?.id ?? '',
      () => canUseModule('work'),
      ...['use', 'create', 'update', 'delete'].map(
        (action) => () => canUseCapability('work', action)
      )
    ],
    (current, previous) => {
      const taskId = tasks.selectedTaskId.value,
        wasActive = locallyActive
      stopLocalActivity()
      tasks.invalidateAccess()
      if (!canUseModule('work') || current[0] !== previous[0]) {
        tasks.clear()
        commands.clear()
        observation.pendingStops.clear()
      } else if (wasActive && taskId) void loadTask(taskId)
    },
    { flush: 'sync' }
  )
  onBeforeUnmount(() => {
    tasks.dispose()
    commands.dispose()
    observation.dispose()
  })
  return reactive({
    tasks: tasks.tasks,
    total: tasks.total,
    filters: tasks.filters,
    listLoading: tasks.listLoading,
    listError: tasks.listError,
    currentTask: tasks.currentTask,
    detailLoading: tasks.detailLoading,
    detailError: tasks.detailError,
    availableAgents: tasks.availableAgents,
    agentsLoading: tasks.agentsLoading,
    agentsError: tasks.agentsError,
    createLoading: commands.createLoading,
    createDraft: commands.createDraft,
    admitting: commands.admitting,
    mutating: commands.mutating,
    stopping: observation.stopping,
    stopAcknowledged: observation.stopAcknowledged,
    runLocked: commands.runLocked,
    canStop: observation.canStop,
    isTaskBusy: commands.isTaskBusy,
    submissionBlocked: commands.submissionBlocked,
    runError: observation.runError,
    retryKind: commands.retryKind,
    disconnected: observation.disconnected,
    loadTasks: tasks.loadTasks,
    loadAvailableAgents: tasks.loadAvailableAgents,
    loadTask,
    clearCurrentTask,
    stopLocalActivity,
    createTask: commands.createTask,
    runMessage: commands.runMessage,
    retryRun: commands.retryRun,
    reconnect: observation.reconnect,
    cancelRun: observation.cancelRun,
    saveTask: commands.saveTask,
    addMember: commands.addMember,
    addMembers: commands.addMembers,
    removeMember: commands.removeMember,
    upgradeMember: commands.upgradeMember
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
