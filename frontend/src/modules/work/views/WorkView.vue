<!-- Adapted from original Work view, MIT licensed. -->
<template>
  <div class="work-view">
    <WorkHistoryDialog v-model:open="historyOpen" />
    <main class="work-view__content">
      <header class="work-view__toolbar">
        <button
          type="button"
          class="work-view__history-button"
          aria-label="工作记录"
          title="工作记录"
          aria-haspopup="dialog"
          aria-controls="work-history-dialog"
          :aria-expanded="historyOpen"
          @click="historyOpen = true"
        >
          <HistoryOutlined aria-hidden="true" />工作记录
        </button>
        <AButton
          v-if="canUseCapability('work', 'create')"
          type="text"
          aria-label="新建工作"
          title="新建工作"
          @click="newWork"
          ><PlusOutlined aria-hidden="true" />新建工作</AButton
        >
      </header>
      <div class="work-view__body">
        <a-alert
          v-if="error || (workStore.currentTask && workStore.detailError)"
          type="error"
          show-icon
          :message="error || workStore.detailError"
        />
        <a-spin
          v-if="taskId && workStore.detailLoading && !workStore.currentTask"
          class="work-view__loading"
        />
        <a-result
          v-else-if="taskId && workStore.detailError && !workStore.currentTask"
          status="error"
          title="工作任务加载失败"
          :sub-title="workStore.detailError"
        >
          <template #extra><a-button @click="loadCurrentTask">重试</a-button></template>
        </a-result>
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
          :can-edit="capabilities.edit"
          :can-run="capabilities.canRun"
          :can-manage-members="capabilities.canManageMembers"
          :available-agents="workStore.availableAgents"
          :agents-loading="workStore.agentsLoading"
          :agents-error="workStore.agentsError"
          @load-agents="loadAgents"
          @send="sendMessage"
          @retry="retryRun"
          @cancel="cancelRun"
          @save="saveTask"
          @status="setStatus"
          @add-members="addMembers"
          @remove-member="removeMember"
          @upgrade-member="upgradeMember"
        />
        <WorkEmptyState
          v-else
          :can-create="capabilities.canCreate"
          :agents="workStore.availableAgents"
          :agents-loading="workStore.agentsLoading"
          :agents-error="workStore.agentsError"
          :creating="workStore.createLoading"
          :initial-draft="workStore.createDraft"
          @load-agents="loadAgents"
          @start="startWork"
        />
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { HistoryOutlined, PlusOutlined } from '@ant-design/icons-vue'
import WorkHistoryDialog from '../components/WorkHistoryDialog.vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Result as AResult,
  Spin as ASpin
} from 'ant-design-vue'
import { identity, canUseCapability } from '../../identity/public.ts'
import { errorMessage, isAborted } from '../../../shared/http.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { useWorkWorkspace } from '../store.ts'
import type { WorkDraft } from '../presentation.ts'
import type { WorkMember, WorkStatus } from '../api/index.ts'
import WorkEmptyState from '../components/WorkEmptyState.vue'
import WorkTaskPage from '../components/WorkTaskPage.vue'
const route = useRoute(),
  router = useRouter(),
  workStore = useWorkWorkspace(),
  confirmation = useConfirmation()
const historyOpen = ref(false)
async function newWork(): Promise<void> {
  workStore.clearCurrentTask()
  await router.push('/work')
  historyOpen.value = false
}
const taskId = computed(() => (typeof route.query.taskId === 'string' ? route.query.taskId : ''))
const canEdit = computed(
  () =>
    canUseCapability('work', 'update') &&
    !!identity.value &&
    (!workStore.currentTask || workStore.currentTask.ownerId === identity.value.id)
)
const capabilities = computed(() => ({
  edit: canEdit.value,
  canRun:
    canUseCapability('work') &&
    (!workStore.currentTask || workStore.currentTask.ownerId === identity.value?.id),
  canManageMembers: canEdit.value,
  canCreate: canUseCapability('work', 'create')
}))
const error = ref('')
let createdNavigationTaskId = ''
async function loadCurrentTask(): Promise<void> {
  if (taskId.value) await workStore.loadTask(taskId.value)
}
async function loadAgents(): Promise<void> {
  await workStore.loadAvailableAgents(taskId.value || null)
}
function display(reason: unknown): void {
  if (!isAborted(reason)) error.value = errorMessage(reason)
}
async function startWork(draft: WorkDraft): Promise<void> {
  error.value = ''
  try {
    const task = await workStore.createTask(draft)
    if (!task || !task.activeAgentId) return
    createdNavigationTaskId = task.taskId
    await router.replace({ path: '/work', query: { taskId: task.taskId } })
    if (taskId.value !== task.taskId) return
    await workStore.loadTask(task.taskId)
    if (taskId.value !== task.taskId || workStore.currentTask?.taskId !== task.taskId) return
    await workStore.runMessage({ agentId: task.activeAgentId, message: task.goal }, true)
  } catch (reason) {
    display(reason)
  }
}
async function sendMessage(
  payload: { agentId: string; message: string },
  clear: () => void
): Promise<void> {
  error.value = ''
  if (await workStore.runMessage(payload)) clear()
}
async function retryRun(): Promise<void> {
  await workStore.retryRun()
}
async function cancelRun(): Promise<void> {
  await workStore.cancelRun()
}
async function saveTask(changes: { title: string; goal: string }, done: () => void): Promise<void> {
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
async function addMembers(ids: string[]): Promise<void> {
  error.value = ''
  try {
    for (const id of ids) {
      if (!(await workStore.addMember(id))) break
    }
  } catch (reason) {
    display(reason)
  }
}
async function removeMember(member: WorkMember): Promise<void> {
  const task = workStore.currentTask
  if (!task) return
  if (
    !(await confirmation.confirm(
      `确定移除“${member.agentNameSnapshot}”吗？历史回复将继续保留。`
    )) ||
    taskId.value !== task.taskId ||
    workStore.currentTask?.version !== task.version
  )
    return
  try {
    await workStore.removeMember(member.agentId)
  } catch (reason) {
    display(reason)
  }
}
async function upgradeMember(member: WorkMember): Promise<void> {
  const task = workStore.currentTask
  if (!task) return
  if (
    !(await confirmation.confirm(
      `确认将“${member.agentNameSnapshot}”从 v${member.publishedVersion} 升级到 v${member.latestPublishedVersion}？`
    )) ||
    taskId.value !== task.taskId ||
    workStore.currentTask?.version !== task.version
  )
    return
  try {
    await workStore.upgradeMember(member)
  } catch (reason) {
    display(reason)
  }
}
watch(
  taskId,
  async (id) => {
    error.value = ''
    confirmation.cancel()
    if (!id) {
      workStore.clearCurrentTask()
      await loadAgents()
      return
    }
    if (createdNavigationTaskId === id) {
      createdNavigationTaskId = ''
      return
    }
    await loadCurrentTask()
  },
  { immediate: true }
)
onBeforeUnmount(() => workStore.stopLocalActivity())
</script>

<style scoped>
.work-view {
  --workspace-font-size-chat: 17px;
  --workspace-line-height-chat: 1.8;
  min-height: 100%;
  height: 100%;
  display: flex;
  position: relative;
  overflow: hidden;
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}
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
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: auto;
}
.work-view__toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  min-height: 70px;
  padding: 12px 24px;
}
.work-view__history-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: var(--workspace-control-height);
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}
.work-view__history-button:hover {
  background: var(--gray-50);
}
.work-view__history-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
@media (max-width: 760px) {
  .work-view__toolbar {
    padding-right: 14px;
    padding-left: 14px;
  }
}
</style>
