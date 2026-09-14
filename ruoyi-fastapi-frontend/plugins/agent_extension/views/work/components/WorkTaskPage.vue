<template>
  <section class="work-task-page">
    <header class="work-task-header">
      <div class="work-task-header__copy">
        <div class="work-task-header__title-row">
          <h1>{{ task.title }}</h1>
          <a-tag :color="statusColor">{{ statusLabel }}</a-tag>
        </div>
        <p>{{ task.goal }}</p>
      </div>
      <div class="work-task-header__actions">
        <a-button @click="infoOpen = true"><InfoCircleOutlined />查看完整目标</a-button>
        <a-tooltip title="编辑标题和目标">
          <a-button v-if="canEdit && task.status === 'active'" :disabled="locked" aria-label="编辑工作信息" @click="openEditor"><EditOutlined /></a-button>
        </a-tooltip>
        <a-button v-if="canEdit && task.status === 'active'" :disabled="locked" @click="emit('status', 'completed')">完成工作</a-button>
        <a-button v-if="canEdit && task.status === 'completed'" @click="emit('status', 'active')">重新打开</a-button>
        <a-button v-if="canEdit && task.status !== 'archived'" :disabled="locked" @click="emit('status', 'archived')">归档</a-button>
      </div>
    </header>

    <WorkMemberBar
      :members="members"
      :can-manage="canManageMembers && task.status === 'active'"
      :can-edit="canEdit && task.status === 'active'"
      :locked="locked"
      @add="openPicker"
      @remove="emit('remove-member', $event)"
      @upgrade="emit('upgrade-member', $event)"
    />

    <a-alert v-if="task.status === 'archived'" type="info" show-icon message="此工作已归档，只能查看历史内容。" />
    <a-alert v-else-if="task.status === 'completed'" type="success" show-icon message="此工作已完成，重新打开后可继续执行。" />
    <a-alert v-if="runError" :type="retryKind === 'unstarted' ? 'info' : 'error'" show-icon :message="runError">
      <template #action>
        <a-button v-if="canRun && task.status === 'active' && !locked" size="small" :danger="retryKind !== 'unstarted'" @click="emit('retry')">
          {{ retryKind === 'unstarted' ? '继续执行' : '重试执行' }}
        </a-button>
      </template>
    </a-alert>

    <WorkMessageTimeline :messages="task.messages" />

    <footer v-if="task.status === 'active' && canRun" class="work-composer">
      <div class="work-composer__controls">
        <a-select
          v-model:value="selectedAgentId"
          aria-label="执行智能体"
          placeholder="选择执行智能体"
          :options="executorOptions"
          :disabled="locked"
        />
        <span v-if="selectedMember && !selectedMember.available" class="work-composer__unavailable">当前智能体不可用</span>
      </div>
      <a-textarea
        v-model:value="message"
        aria-label="工作消息"
        placeholder="输入要交给当前智能体的工作消息"
        :maxlength="32000"
        :auto-size="{ minRows: 2, maxRows: 7 }"
        :disabled="locked"
        @keydown.enter.exact.prevent="send"
      />
      <div class="work-composer__submit">
        <a-button v-if="locked" danger @click="emit('cancel')"><PauseCircleOutlined />停止</a-button>
        <a-button v-else type="primary" :disabled="!canSend" @click="send"><SendOutlined />发送</a-button>
      </div>
    </footer>
    <a-alert v-else-if="task.status === 'active'" type="info" show-icon message="当前账号可查看工作历史，但没有执行权限。" />

    <WorkAgentPicker
      v-model:open="pickerOpen"
      :agents="availableAgents"
      :selected-ids="members.map(member => member.agentId)"
      :loading="agentsLoading"
      :error="agentsError"
      @confirm="addSelectedMembers"
    />

    <a-modal v-model:open="editorOpen" title="编辑工作信息" @ok="saveEditor">
      <a-form layout="vertical">
        <a-form-item label="标题"><a-input v-model:value="editingTitle" :maxlength="100" /></a-form-item>
        <a-form-item label="完整目标"><a-textarea v-model:value="editingGoal" :maxlength="32000" :rows="7" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:open="infoOpen" title="工作信息" :footer="null">
      <h2 class="work-info-title">{{ task.title }}</h2>
      <p class="work-info-goal">{{ task.goal }}</p>
    </a-modal>
  </section>
</template>

<script setup>
import { EditOutlined, InfoCircleOutlined, PauseCircleOutlined, SendOutlined } from '@ant-design/icons-vue'
import { validateWorkMessage, validateWorkTitle, validateWorkGoal } from '../../../utils/workTask.js'
import WorkAgentPicker from './WorkAgentPicker.vue'
import WorkMemberBar from './WorkMemberBar.vue'
import WorkMessageTimeline from './WorkMessageTimeline.vue'

const props = defineProps({
  task: { type: Object, required: true },
  locked: { type: Boolean, default: false },
  runError: { type: String, default: '' },
  retryKind: { type: String, default: '' },
  canEdit: { type: Boolean, default: false },
  canRun: { type: Boolean, default: false },
  canManageMembers: { type: Boolean, default: false },
  availableAgents: { type: Array, default: () => [] },
  agentsLoading: { type: Boolean, default: false },
  agentsError: { type: String, default: '' },
})
const emit = defineEmits(['add-members', 'cancel', 'load-agents', 'remove-member', 'retry', 'save', 'send', 'status', 'upgrade-member'])
const message = ref('')
const selectedAgentId = ref(props.task.activeAgentId || null)
const pickerOpen = ref(false)
const editorOpen = ref(false)
const infoOpen = ref(false)
const editingTitle = ref('')
const editingGoal = ref('')
const members = computed(() => (props.task.members || []).filter((member) => !member.removedAt))
const executorOptions = computed(() => members.value.map((member) => ({
  value: member.agentId,
  label: member.agentNameSnapshot,
  disabled: !member.available,
})))
const selectedMember = computed(() => members.value.find((member) => member.agentId === selectedAgentId.value))
const canSend = computed(() => validateWorkMessage(message.value).valid && selectedMember.value?.available)
const statusLabel = computed(() => ({ active: '进行中', completed: '已完成', archived: '已归档' }[props.task.status] || props.task.status))
const statusColor = computed(() => ({ active: 'processing', completed: 'success', archived: 'default' }[props.task.status]))

watch(() => props.task.activeAgentId, (value) => {
  if (!members.value.some((member) => member.agentId === selectedAgentId.value && member.available)) {
    selectedAgentId.value = members.value.find((member) => member.available)?.agentId || value || null
  }
}, { immediate: true })

function send() {
  if (!canSend.value) return
  const content = message.value.trim()
  emit('send', { agentId: selectedAgentId.value, message: content }, () => { message.value = '' })
}
function openPicker() {
  pickerOpen.value = true
  emit('load-agents')
}
function addSelectedMembers(ids) {
  const existing = new Set(members.value.map((member) => member.agentId))
  emit('add-members', ids.filter((id) => !existing.has(id)))
}
function openEditor() {
  editingTitle.value = props.task.title
  editingGoal.value = props.task.goal
  editorOpen.value = true
}
function saveEditor() {
  if (!validateWorkTitle(editingTitle.value).valid || !validateWorkGoal(editingGoal.value).valid) return
  emit('save', { title: editingTitle.value.trim(), goal: editingGoal.value.trim() })
  editorOpen.value = false
}
</script>

<style scoped>
.work-task-page { min-height: 100%; height: 100%; display: flex; flex-direction: column; overflow: hidden; background: var(--workspace-bg); color: var(--color-text); font-family: var(--workspace-font-family); }
.work-task-header { min-width: 0; display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 16px 20px; border-bottom: 1px solid var(--gray-150); background: var(--bg-container); }
.work-task-header__copy { min-width: 0; flex: 1; }
.work-task-header__title-row { min-width: 0; display: flex; align-items: flex-start; gap: 8px; }
.work-task-header h1 { min-width: 0; display: -webkit-box; overflow: hidden; margin: 0; color: var(--color-text); font-size: 18px; line-height: 1.45; overflow-wrap: anywhere; letter-spacing: 0; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.work-task-header p { display: -webkit-box; overflow: hidden; margin: 6px 0 0; color: var(--color-text-secondary); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; white-space: pre-wrap; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
.work-task-header__actions { flex: 0 0 auto; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.work-task-page > :deep(.ant-alert) { border-radius: 0; }
.work-composer { width: min(860px, calc(100% - 32px)); align-self: center; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 12px; padding: 12px 0 16px; }
.work-composer__controls { grid-column: 1 / -1; min-width: 0; display: flex; align-items: center; gap: 10px; }
.work-composer__controls :deep(.ant-select) { width: min(320px, 100%); }
.work-composer__unavailable { color: var(--workspace-danger); font-size: 12px; }
.work-composer__submit { display: flex; align-items: flex-end; }
.work-composer__submit :deep(.ant-btn) { min-width: 82px; }
.work-info-title, .work-info-goal { overflow-wrap: anywhere; white-space: pre-wrap; }
.work-info-title { margin-top: 0; font-size: 18px; letter-spacing: 0; }
.work-info-goal { max-height: 55vh; overflow-y: auto; line-height: 1.7; }
@media (max-width: 640px) { .work-task-header { padding: 12px; flex-direction: column; } .work-task-header__actions { width: 100%; justify-content: flex-start; } .work-composer { width: calc(100% - 24px); grid-template-columns: minmax(0, 1fr) auto; } .work-composer__controls { align-items: flex-start; flex-direction: column; } }
</style>
