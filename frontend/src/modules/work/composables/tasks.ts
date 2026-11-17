import { ref, reactive } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import * as api from '../api/index.ts'
import type { WorkFilters, WorkOptions, WorkSummary, WorkTask } from '../api/index.ts'

interface TaskResourcesOptions {
  canRead: () => boolean
  recordTask: (task: WorkSummary) => void
  projectCurrentRun: () => void
}

export function useWorkTasks(options: TaskResourcesOptions) {
  const tasks = ref<WorkSummary[]>([]),
    total = ref(0)
  const filters = reactive<WorkFilters>({ keyword: '', status: '', pageNum: 1, pageSize: 20 })
  const listLoading = ref(false),
    listError = ref('')
  const selectedTaskId = ref(''),
    currentTask = ref<WorkTask | null>(null)
  const detailLoading = ref(false),
    detailError = ref('')
  const workOptions = ref<WorkOptions | null>(null),
    optionsLoading = ref(false),
    optionsError = ref('')
  const listScope = new RequestScope(),
    taskScope = new RequestScope(),
    optionsScope = new RequestScope()

  function acceptTask(task: WorkTask, context: ContextTicket): WorkTask | null {
    if (!context.isCurrent() || selectedTaskId.value !== task.taskId) return null
    const current = currentTask.value
    if (current?.taskId === task.taskId && current.version > task.version) return current
    currentTask.value = task
    options.recordTask(task)
    options.projectCurrentRun()
    return task
  }

  async function loadTasks(query?: WorkFilters): Promise<void> {
    if (!options.canRead()) return
    if (query) Object.assign(filters, query)
    const context = listScope.replace()
    listLoading.value = true
    listError.value = ''
    try {
      const result = await api.listTasks({ ...filters }, context.signal)
      if (!context.isCurrent()) return
      result.rows.forEach(options.recordTask)
      tasks.value = result.rows
      total.value = result.total
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) listError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) listLoading.value = false
    }
  }

  async function loadOptions(): Promise<void> {
    if (!options.canRead()) return
    const context = optionsScope.replace()
    optionsLoading.value = true
    optionsError.value = ''
    try {
      const result = await api.getWorkOptions(context.signal)
      if (context.isCurrent()) workOptions.value = result
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) optionsError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) optionsLoading.value = false
    }
  }

  async function refreshTask(taskId: string, context: ContextTicket): Promise<WorkTask | null> {
    return acceptTask(await api.getTask(taskId, context.signal), context)
  }

  async function loadTask(taskId: string): Promise<WorkTask | null> {
    if (!options.canRead()) return null
    selectedTaskId.value = taskId
    currentTask.value = null
    const context = taskScope.current()
    detailLoading.value = true
    detailError.value = ''
    try {
      return await refreshTask(taskId, context)
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) detailError.value = errorMessage(reason)
      return null
    } finally {
      if (context.isCurrent()) detailLoading.value = false
    }
  }

  function stopLocalActivity(): void {
    taskScope.replace()
    optionsScope.replace()
    detailLoading.value = false
    optionsLoading.value = false
  }
  function clearCurrentTask(): void {
    selectedTaskId.value = ''
    currentTask.value = null
    detailError.value = ''
  }
  function invalidateAccess(): void {
    stopLocalActivity()
    listScope.replace()
    listLoading.value = false
  }
  function clear(): void {
    invalidateAccess()
    clearCurrentTask()
    tasks.value = []
    workOptions.value = null
    optionsError.value = ''
    total.value = 0
    listError.value = ''
  }
  function dispose(): void {
    for (const scope of [listScope, taskScope, optionsScope]) scope.dispose()
  }

  return {
    tasks,
    total,
    filters,
    listLoading,
    listError,
    selectedTaskId,
    currentTask,
    detailLoading,
    detailError,
    workOptions,
    optionsLoading,
    optionsError,
    taskScope,
    acceptTask,
    loadTasks,
    loadOptions,
    refreshTask,
    loadTask,
    stopLocalActivity,
    clearCurrentTask,
    invalidateAccess,
    clear,
    dispose
  }
}
export type WorkTasks = ReturnType<typeof useWorkTasks>
