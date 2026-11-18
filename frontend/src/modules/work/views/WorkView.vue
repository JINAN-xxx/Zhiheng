<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { HistoryOutlined, PlusOutlined } from '@ant-design/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Result as AResult,
  Spin as ASpin
} from 'ant-design-vue'
import { identity, canUseCapability } from '../../identity/public.ts'
import { errorMessage, isAborted } from '../../../shared/http.ts'
import { useWorkWorkspace } from '../store.ts'
import type { WorkDraft } from '../presentation.ts'
import { MAX_WORK_FILE_BYTES } from '../presentation.ts'
import type { UpdateWorkBody, WorkStatus, WorkTask } from '../api/index.ts'
import type { WorkTurnInput } from '../composables/turns.ts'
import WorkHistoryDialog from '../components/WorkHistoryDialog.vue'
import WorkEmptyState from '../components/WorkEmptyState.vue'
import WorkTaskPage from '../components/WorkTaskPage.vue'

const route = useRoute(),
  router = useRouter(),
  workStore = useWorkWorkspace()
const historyOpen = ref(false),
  error = ref(''),
  draftMessage = ref('')
const taskId = computed(() => (typeof route.query.taskId === 'string' ? route.query.taskId : ''))
const ownsTask = computed(
  () => !workStore.currentTask || workStore.currentTask.ownerId === identity.value?.id
)
const canEdit = computed(
  () =>
    canUseCapability('work', 'update') && ownsTask.value && workStore.currentTask?.mode === 'agent'
)
const canRun = computed(
  () => canUseCapability('work', 'use') && ownsTask.value && workStore.currentTask?.mode === 'agent'
)
let createdNavigationTaskId = ''
function display(reason: unknown): void {
  if (!isAborted(reason)) error.value = errorMessage(reason)
}
async function newWork(): Promise<void> {
  workStore.clearCurrentTask()
  draftMessage.value = ''
  await router.push('/work')
  historyOpen.value = false
}
async function loadCurrentTask(): Promise<void> {
  if (taskId.value) await Promise.all([workStore.loadTask(taskId.value), workStore.loadOptions()])
}
async function navigateCreated(task: WorkTask): Promise<boolean> {
  createdNavigationTaskId = task.taskId
  await router.replace({ path: '/work', query: { taskId: task.taskId } })
  if (taskId.value !== task.taskId) return false
  await workStore.loadTask(task.taskId)
  return taskId.value === task.taskId && workStore.currentTask?.taskId === task.taskId
}
async function startWork(draft: WorkDraft): Promise<void> {
  error.value = ''
  draftMessage.value = ''
  try {
    const task = await workStore.createTask(draft)
    if (task && (await navigateCreated(task)))
      await workStore.runMessage({ message: task.goal }, true)
  } catch (reason) {
    display(reason)
  }
}
async function uploadFirst(draft: WorkDraft, files: File[]): Promise<void> {
  error.value = ''
  if (files.some((file) => file.size < 1 || file.size > MAX_WORK_FILE_BYTES)) {
    error.value = '请选择非空文件，单个文件不超过 20 MiB'
    return
  }
  try {
    draftMessage.value = draft.goal
    const task = await workStore.createTask({ ...draft, goal: '' })
    if (task && (await navigateCreated(task))) await workStore.uploadFiles(files)
  } catch (reason) {
    display(reason)
  }
}
async function sendMessage(payload: WorkTurnInput, restore: () => void): Promise<void> {
  error.value = ''
  draftMessage.value = ''
  const accepted = await workStore.runMessage(payload)
  if (!accepted && workStore.retryKind !== 'uncertain' && !workStore.disconnected) restore()
}
async function uploadFiles(files: File[]): Promise<void> {
  error.value = ''
  try {
    await workStore.uploadFiles(files)
  } catch (reason) {
    display(reason)
  }
}
async function saveTask(
  changes: Omit<UpdateWorkBody, 'expected_version'>,
  done: () => void
): Promise<void> {
  error.value = ''
  try {
    if (await workStore.saveTask(changes)) done()
  } catch (reason) {
    display(reason)
  }
}
async function setStatus(status: WorkStatus): Promise<void> {
  error.value = ''
  try {
    await workStore.saveTask({ status })
  } catch (reason) {
    display(reason)
  }
}
watch(
  taskId,
  async (id) => {
    error.value = ''
    if (!id) {
      draftMessage.value = ''
      workStore.clearCurrentTask()
      await workStore.loadOptions()
      return
    }
    if (createdNavigationTaskId === id) {
      createdNavigationTaskId = ''
      return
    }
    draftMessage.value = ''
    await loadCurrentTask()
  },
  { immediate: true }
)
onBeforeUnmount(() => workStore.stopLocalActivity())
</script>
<template>
  <div class="work-view">
    <WorkHistoryDialog v-model:open="historyOpen" />
    <main class="work-view__content">
      <header class="chat-toolbar work-view__toolbar">
        <button
          type="button"
          class="chat-toolbar-action"
          aria-label="工作记录"
          title="工作记录"
          aria-haspopup="dialog"
          aria-controls="work-history-dialog"
          :aria-expanded="historyOpen"
          @click="historyOpen = true"
        >
          <HistoryOutlined aria-hidden="true" /><span class="chat-toolbar__label"
            >工作记录</span
          ></button
        ><AButton
          v-if="canUseCapability('work', 'create')"
          type="text"
          class="chat-toolbar-action"
          aria-label="新建工作"
          title="新建工作"
          @click="newWork"
          ><PlusOutlined aria-hidden="true" /><span class="chat-toolbar__label"
            >新建工作</span
          ></AButton
        >
      </header>
      <div class="work-view__body">
        <AAlert
          v-if="error || (workStore.currentTask && workStore.detailError)"
          class="work-view__error"
          type="error"
          show-icon
          :message="error || workStore.detailError"
        />
        <ASpin
          v-if="taskId && workStore.detailLoading && !workStore.currentTask"
          class="work-view__loading"
        />
        <AResult
          v-else-if="taskId && workStore.detailError && !workStore.currentTask"
          status="error"
          title="工作任务加载失败"
          :sub-title="workStore.detailError"
          ><template #extra><AButton @click="loadCurrentTask">重试</AButton></template></AResult
        >
        <WorkTaskPage
          v-else-if="taskId && workStore.currentTask?.taskId === taskId"
          :key="taskId"
          :task="workStore.currentTask"
          :locked="workStore.submissionBlocked"
          :stoppable="workStore.canStop"
          :stop-disabled="workStore.stopping || workStore.admitting || workStore.stopAcknowledged"
          :can-recover="workStore.disconnected"
          :run-error="workStore.runError"
          :retry-kind="workStore.retryKind"
          :can-edit="canEdit"
          :can-run="canRun"
          :options="workStore.workOptions"
          :options-loading="workStore.optionsLoading"
          :options-error="workStore.optionsError"
          :uploading="workStore.uploading"
          :mutating="workStore.mutating"
          :initial-message="draftMessage"
          @retry-options="workStore.loadOptions"
          @send="sendMessage"
          @retry="workStore.retryRun"
          @cancel="workStore.cancelRun"
          @upload="uploadFiles"
          @save="saveTask"
          @status="setStatus"
        />
        <WorkEmptyState
          v-else
          :can-create="canUseCapability('work', 'create')"
          :options="workStore.workOptions"
          :options-loading="workStore.optionsLoading"
          :options-error="workStore.optionsError"
          :creating="workStore.createLoading"
          :uncertain="workStore.createUncertain"
          :initial-draft="workStore.createDraft"
          @retry-options="workStore.loadOptions"
          @start="startWork"
          @upload="uploadFirst"
        />
      </div>
    </main>
  </div>
</template>
<style scoped lang="scss">
@use '../../../shared/styles/conversation' as conversation;
@include conversation.page('.work-view');
@include conversation.toolbar;
.work-view__loading {
  margin: auto;
}
.work-view__content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.work-view__body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}
.work-view__body > :deep(.work-task-page) {
  flex: 1;
  min-height: 0;
}
.work-view__error {
  margin: 8px 24px;
  flex-shrink: 0;
}
</style>
