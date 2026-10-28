<!-- Adapted from original Work view, MIT licensed. -->
<template>
  <div class="work-view">
    <aside v-show="historyVisible" class="work-history" aria-label="工作任务列表">
      <header>
        <h2>工作</h2>
        <AButton
          v-if="compactHistory"
          type="text"
          aria-label="收起工作列表"
          @click="historyVisible = false"
          ><CloseOutlined
        /></AButton>
      </header>
      <AButton class="work-history__new" aria-label="新建工作" @click="newWork"
        ><PlusOutlined />新建工作</AButton
      >
      <WorkNavigation @navigate="closeHistory" />
    </aside>
    <button
      v-if="compactHistory && historyVisible"
      type="button"
      class="work-history__scrim"
      aria-label="关闭工作列表"
      @click="historyVisible = false"
    />
    <main class="work-view__content">
      <div class="work-view__mobile-tools">
        <AButton
          type="text"
          :aria-label="historyVisible ? '收起工作列表' : '展开工作列表'"
          @click="historyVisible = !historyVisible"
          ><MenuOutlined />工作列表</AButton
        >
      </div>
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
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import { MenuOutlined, PlusOutlined, CloseOutlined } from '@ant-design/icons-vue'
import WorkNavigation from '../components/WorkNavigation.vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Result as AResult,
  Spin as ASpin
} from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
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
const compactHistory = useMediaQuery('(max-width: 760px)')
const historyVisible = ref(!compactHistory.value)
watch(compactHistory, (compact) => {
  if (!compact) historyVisible.value = true
})
function closeHistory(): void {
  if (compactHistory.value) historyVisible.value = false
}
async function newWork(): Promise<void> {
  workStore.clearCurrentTask()
  await router.push('/work')
  closeHistory()
}
const taskId = computed(() => (typeof route.query.taskId === 'string' ? route.query.taskId : ''))
const canEdit = computed(
  () =>
    !!identity.value &&
    (!workStore.currentTask ||
      workStore.currentTask.ownerId === identity.value.id ||
      identity.value.is_admin)
)
const capabilities = computed(() => ({
  edit: canEdit.value,
  canRun: canEdit.value,
  canManageMembers: canEdit.value,
  canCreate: !!identity.value
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
  min-height: 100%;
  height: 100%;
  display: flex;
  position: relative;
  overflow: hidden;
  background: var(--bg-container);
}
.work-view__loading {
  margin: auto;
}
.work-history {
  display: flex;
  flex-direction: column;
  flex: 0 0 240px;
  width: 240px;
  min-height: 0;
  border-right: 1px solid var(--gray-150);
  background: var(--gray-25);
}
.work-history > header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 50px;
  padding: 10px 14px 2px;
}
.work-history h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.work-history__new {
  margin: 5px 12px 0;
  color: var(--main-color);
  border-color: var(--main-200);
}
.work-view__content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.work-view__mobile-tools {
  display: none;
}
.work-history__scrim {
  position: absolute;
  inset: 0;
  z-index: 10;
  border: 0;
  padding: 0;
  background: rgb(25 26 40 / 12%);
}
@media (max-width: 760px) {
  .work-history {
    position: absolute;
    inset: 0 auto 0 0;
    z-index: 11;
    width: 240px;
  }
  .work-view__mobile-tools {
    display: block;
    padding: 4px 8px;
    border-bottom: 1px solid var(--gray-150);
  }
}
</style>
