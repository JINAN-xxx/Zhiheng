<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal
} from 'ant-design-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { identity, canUseCapability } from '../../identity/public.ts'
import { deleteTask, updateTask } from '../api/index.ts'
import type { WorkSummary } from '../api/index.ts'
import { validateWorkTitle } from '../presentation.ts'
import { useWorkWorkspace } from '../store.ts'
import RecentWorkTasks from './RecentWorkTasks.vue'
const state = useWorkWorkspace(),
  route = useRoute(),
  router = useRouter(),
  confirmation = useConfirmation()
const emit = defineEmits<{ navigate: [] }>()
const rename = ref<WorkSummary | null>(null),
  title = ref(''),
  error = ref(''),
  working = ref(false)
const scope = new RequestScope()
onMounted(() => {
  void state.loadTasks()
})
function close(): void {
  scope.replace()
  rename.value = null
  working.value = false
  error.value = ''
}
watch(
  () => route.fullPath,
  () => {
    close()
    confirmation.cancel()
  }
)
onBeforeUnmount(() => scope.dispose())
function open(id: string): void {
  void router.push({ path: '/work', query: { taskId: id } })
  emit('navigate')
}
function openRename(task: WorkSummary): void {
  if (!canManageTask(task)) return
  close()
  rename.value = task
  title.value = task.title
}
async function save(): Promise<void> {
  const task = rename.value
  if (!task || working.value) return
  if (!canManageTask(task)) {
    error.value = '任务正在运行或当前账号无法修改此任务'
    return
  }
  if (!validateWorkTitle(title.value).valid) {
    error.value = '标题须为1到100字'
    return
  }
  const context = scope.replace()
  working.value = true
  error.value = ''
  try {
    await updateTask(
      task.taskId,
      { title: title.value.trim(), expected_version: task.version },
      context.signal
    )
    if (!context.isCurrent()) return
    rename.value = null
    await state.loadTasks()
    if (context.isCurrent() && state.currentTask?.taskId === task.taskId)
      await state.loadTask(task.taskId)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) working.value = false
  }
}
async function remove(task: WorkSummary): Promise<void> {
  if (
    !canManageTask(task, 'delete') ||
    !(await confirmation.confirm(`删除工作“${task.title}”？历史内容将不再显示。`)) ||
    !canManageTask(task, 'delete')
  )
    return
  const context = scope.replace()
  working.value = true
  error.value = ''
  try {
    await deleteTask(task.taskId, task.version, context.signal)
    if (!context.isCurrent()) return
    if (route.query.taskId === task.taskId) await router.push('/work')
    await state.loadTasks()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) working.value = false
  }
}
function canManageTask(task: WorkSummary, action: 'update' | 'delete' = 'update'): boolean {
  if (!canUseCapability('work', action)) return false
  if (
    working.value ||
    task.currentRunId ||
    state.isTaskBusy(task.taskId) ||
    !identity.value ||
    task.ownerId !== identity.value.id
  )
    return false
  if (
    route.query.taskId === task.taskId &&
    (!state.currentTask || state.currentTask.taskId !== task.taskId || state.submissionBlocked)
  )
    return false
  return true
}
</script>
<template>
  <AAlert v-if="error && !rename" :message="error" type="error" show-icon />
  <RecentWorkTasks
    :tasks="state.tasks"
    :active-task-id="String(route.query.taskId ?? '')"
    :loading="state.listLoading"
    :error="state.listError"
    :total="state.total"
    :page-num="state.filters.pageNum"
    :page-size="state.filters.pageSize"
    :keyword="state.filters.keyword"
    :status="state.filters.status"
    can-open
    :can-edit="!working"
    :can-remove="!working"
    :can-manage-task="canManageTask"
    @filter="state.loadTasks"
    @open="open"
    @rename="openRename"
    @delete="remove"
  />
  <AModal
    :open="rename !== null"
    title="重命名工作"
    :confirm-loading="working"
    @ok="save"
    @cancel="close"
  >
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <AForm layout="vertical"
      ><AFormItem label="工作标题" name="title"
        ><AInput
          v-model:value="title"
          :maxlength="200"
          :disabled="working"
          @press-enter="save" /></AFormItem
    ></AForm>
  </AModal>
</template>
