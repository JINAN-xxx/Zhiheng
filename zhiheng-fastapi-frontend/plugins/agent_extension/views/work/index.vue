<template>
  <div class="work-view">
    <a-spin v-if="taskId && workStore.detailLoading && !workStore.currentTask" class="work-view__loading" />
    <a-result v-else-if="taskId && workStore.detailError && !workStore.currentTask" status="error" title="工作任务加载失败" :sub-title="workStore.detailError">
      <template #extra><a-button @click="loadCurrentTask">重试</a-button></template>
    </a-result>
    <WorkTaskPage
      v-else-if="taskId && workStore.currentTask?.taskId === taskId"
      :key="taskId"
      :task="workStore.currentTask"
      :locked="workStore.runLocked"
      :run-error="workStore.runError"
      :retry-kind="workStore.retryAttempt?.kind || ''"
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
      :can-chat="canChat"
      :can-create="capabilities.canCreate"
      :agents="workStore.availableAgents"
      :agents-loading="workStore.agentsLoading"
      :agents-error="workStore.agentsError"
      :creating="workStore.createLoading"
      :initial-draft="workStore.createDraft"
      @mode="switchMode"
      @load-agents="loadAgents"
      @start="startWork"
    />
  </div>
</template>

<script setup name="AiWork">
import feedback from '@/plugins/antdFeedback'
import useUserStore from '@/store/modules/user'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import { useWorkTaskStore, resolveWorkTaskCapabilities } from '../../public/workTasks.js'
import WorkEmptyState from './components/WorkEmptyState.vue'
import WorkTaskPage from './components/WorkTaskPage.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const workStore = useWorkTaskStore()
const permissionSet = computed(() => new Set(userStore.permissions || []))
const capabilities = computed(() => resolveWorkTaskCapabilities(permissionSet.value))
const canChat = computed(() => permissionSet.value.has('*:*:*') || permissionSet.value.has('ai:chat:list'))
const taskId = computed(() => String(route.query.taskId || ''))
const createdNavigationTaskId = ref('')

async function loadCurrentTask() {
  if (!taskId.value || !capabilities.value.query) return
  await workStore.loadTask(taskId.value)
}

async function loadAgents() {
  if (!capabilities.value.useAgent || workStore.agentsLoading) return
  try { await workStore.loadAvailableAgents() } catch {}
}

async function startWork(draft) {
  try {
    const created = await workStore.createTask(draft)
    if (!created) return
    createdNavigationTaskId.value = created.task.taskId
    await router.replace({ path: '/ai/chat/work', query: { taskId: created.task.taskId } })
    await workStore.runMessage(created.run)
  } catch (error) {
    feedback.msgError(normalizeErrorMessage(error, '工作创建失败'))
  }
}

async function sendMessage(payload, clear) {
  try {
    const completed = await workStore.runMessage(payload)
    if (completed) clear?.()
  } catch (error) {
    feedback.msgError(normalizeErrorMessage(error, '工作执行失败'))
  }
}

async function retryRun() {
  try {
    await workStore.retryRun({ confirmedFailed: workStore.retryAttempt?.kind === 'failed' })
  } catch (error) {
    feedback.msgError(normalizeErrorMessage(error, '重试失败'))
  }
}

async function cancelRun() {
  try { await workStore.cancelRun() } catch (error) { feedback.msgError(normalizeErrorMessage(error, '停止失败')) }
}
async function saveTask(changes) {
  try { await workStore.saveTask(changes); await workStore.loadTasks() } catch (error) { feedback.msgError(normalizeErrorMessage(error, '保存失败')) }
}
async function setStatus(status) {
  try { await workStore.saveTask({ status }); await workStore.loadTasks() } catch (error) { feedback.msgError(normalizeErrorMessage(error, '状态更新失败')) }
}
async function addMembers(agentIds) {
  try { for (const agentId of agentIds) await workStore.addMember(agentId) } catch (error) { feedback.msgError(normalizeErrorMessage(error, '添加智能体失败')) }
}
async function removeMember(member) {
  try {
    await feedback.confirm(`确定移除“${member.agentNameSnapshot}”吗？历史回复将继续保留。`)
    await workStore.removeMember(member.agentId)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') feedback.msgError(normalizeErrorMessage(error, '移除失败'))
  }
}
async function upgradeMember(member) {
  try {
    await feedback.confirm(`确认将“${member.agentNameSnapshot}”从 v${member.publishedVersion} 升级到 v${member.latestPublishedVersion}？`)
    await workStore.upgradeMember(member)
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') feedback.msgError(normalizeErrorMessage(error, '升级失败'))
  }
}
function switchMode(mode) {
  if (mode === 'chat') router.push('/ai/chat')
}

watch(taskId, async (nextTaskId, previousTaskId) => {
  if (nextTaskId === previousTaskId && workStore.currentTask?.taskId === nextTaskId) return
  if (!nextTaskId) {
    workStore.clearCurrentTask()
    return
  }
  if (createdNavigationTaskId.value === nextTaskId) {
    createdNavigationTaskId.value = ''
    return
  }
  if (capabilities.value.query) await loadCurrentTask()
}, { immediate: true })

onBeforeUnmount(() => workStore.stopLocalActivity())
</script>

<style scoped>
.work-view { min-height: 100%; height: 100%; display: flex; flex-direction: column; overflow-x: hidden; overflow-y: auto; background: var(--gray-10); }
.work-view__loading { margin: auto; }
</style>
