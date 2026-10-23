<!-- Adapted from original Work UI, MIT licensed. -->
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
        <a-button aria-label="查看完整目标" @click="infoOpen = true"
          ><InfoCircleOutlined />查看完整目标</a-button
        >
        <a-tooltip title="编辑标题和目标">
          <a-button
            v-if="canEdit && task.status === 'active'"
            :disabled="locked"
            aria-label="编辑工作信息"
            @click="openEditor"
            ><EditOutlined
          /></a-button>
        </a-tooltip>
        <a-button
          v-if="canEdit && task.status === 'active'"
          :disabled="locked"
          @click="emit('status', 'completed')"
          >完成工作</a-button
        >
        <a-button v-if="canEdit && task.status === 'completed'" @click="emit('status', 'active')"
          >重新打开</a-button
        >
        <a-button
          v-if="canEdit && task.status !== 'archived'"
          aria-label="归档"
          :disabled="locked"
          @click="emit('status', 'archived')"
          >归档</a-button
        >
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

    <a-alert
      v-if="task.status === 'archived'"
      type="info"
      show-icon
      message="此工作已归档，只能查看历史内容。"
    />
    <a-alert
      v-else-if="task.status === 'completed'"
      type="success"
      show-icon
      message="此工作已完成，重新打开后可继续执行。"
    />
    <a-alert
      v-if="runError"
      :type="retryKind === 'unstarted' ? 'info' : 'error'"
      show-icon
      :message="runError"
    >
      <template #action>
        <a-button
          v-if="
            canRun &&
            task.status === 'active' &&
            (canRecover || retryKind === 'uncertain' || (!!retryKind && !locked))
          "
          size="small"
          :danger="retryKind !== 'unstarted'"
          @click="emit('retry')"
        >
          {{ canRecover ? '恢复查看' : retryKind === 'unstarted' ? '继续执行' : '重试执行' }}
        </a-button>
      </template>
    </a-alert>

    <WorkMessageTimeline :messages="task.messages" />

    <footer v-if="task.status === 'active' && canRun" class="work-composer">
      <div class="work-composer__controls">
        <a-select
          :value="selectedAgentId ?? undefined"
          aria-label="执行智能体"
          placeholder="选择执行智能体"
          :options="executorOptions"
          :disabled="locked"
          @update:value="setSelectedAgent"
        />
        <span v-if="selectedMember && !selectedMember.available" class="work-composer__unavailable"
          >当前智能体不可用</span
        >
      </div>
      <a-textarea
        v-model:value="message"
        aria-label="工作消息"
        placeholder="输入要交给当前智能体的工作消息"
        :maxlength="64000"
        :auto-size="{ minRows: 2, maxRows: 7 }"
        :disabled="locked"
        @keydown="keydown"
      />
      <div class="work-composer__submit">
        <a-button
          v-if="stoppable"
          aria-label="停止"
          :disabled="stopDisabled"
          danger
          @click="emit('cancel')"
          ><PauseCircleOutlined />停止</a-button
        >
        <a-button
          v-else
          aria-label="发送"
          type="primary"
          :disabled="!canSend || locked"
          @click="send"
          ><SendOutlined />发送</a-button
        >
      </div>
    </footer>
    <a-alert
      v-else-if="task.status === 'active'"
      type="info"
      show-icon
      message="当前账号可查看工作历史，但没有执行权限。"
    />

    <WorkAgentPicker
      v-model:open="pickerOpen"
      :agents="availableAgents"
      :selected-ids="members.map((member) => member.agentId)"
      :loading="agentsLoading"
      :error="agentsError"
      @confirm="addSelectedMembers"
    />

    <a-modal v-model:open="editorOpen" title="编辑工作信息" @ok="saveEditor">
      <a-form layout="vertical">
        <a-form-item label="标题" name="title"
          ><a-input v-model:value="editingTitle" :maxlength="200"
        /></a-form-item>
        <a-form-item label="完整目标" name="goal"
          ><a-textarea v-model:value="editingGoal" :maxlength="64000" :rows="7"
        /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:open="infoOpen" title="工作信息" :footer="null">
      <h2 class="work-info-title">{{ task.title }}</h2>
      <p class="work-info-goal">{{ task.goal }}</p>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Select as ASelect,
  Tag as ATag,
  Textarea as ATextarea,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  EditOutlined,
  InfoCircleOutlined,
  PauseCircleOutlined,
  SendOutlined
} from '@ant-design/icons-vue'
import { validateWorkMessage, validateWorkTitle, validateWorkGoal } from '../presentation.ts'
import type { WorkAgent, WorkMember, WorkTask, WorkStatus } from '../api/index.ts'
import WorkAgentPicker from './WorkAgentPicker.vue'
import WorkMemberBar from './WorkMemberBar.vue'
import WorkMessageTimeline from './WorkMessageTimeline.vue'
const props = defineProps<{
  task: WorkTask
  locked: boolean
  stoppable: boolean
  stopDisabled: boolean
  canRecover: boolean
  runError: string
  retryKind: string
  canEdit: boolean
  canRun: boolean
  canManageMembers: boolean
  availableAgents: WorkAgent[]
  agentsLoading: boolean
  agentsError: string
}>()
const emit = defineEmits<{
  'add-members': [ids: string[]]
  cancel: []
  'load-agents': []
  'remove-member': [member: WorkMember]
  retry: []
  save: [changes: { title: string; goal: string }, done: () => void]
  send: [payload: { agentId: string; message: string }, clear: () => void]
  status: [status: WorkStatus]
  'upgrade-member': [member: WorkMember]
}>()
const message = ref(''),
  selectedAgentId = ref<string | null>(props.task.activeAgentId)
const pickerOpen = ref(false),
  editorOpen = ref(false),
  infoOpen = ref(false),
  editingTitle = ref(''),
  editingGoal = ref('')
const members = computed(() => props.task.members.filter((member) => !member.removedAt))
const executorOptions = computed(() =>
  members.value.map((member) => ({
    value: member.agentId,
    label: member.agentNameSnapshot,
    disabled: !member.available
  }))
)
const selectedMember = computed(() =>
  members.value.find((member) => member.agentId === selectedAgentId.value)
)
const canSend = computed(
  () => !props.locked && validateWorkMessage(message.value).valid && selectedMember.value?.available
)
const statusLabel = computed(
  () => ({ active: '进行中', completed: '已完成', archived: '已归档' })[props.task.status]
)
const statusColor = computed(
  () => ({ active: 'processing', completed: 'success', archived: 'default' })[props.task.status]
)
watch(
  () => [
    props.task.activeAgentId,
    ...members.value.map((member) => `${member.agentId}:${member.available}`)
  ],
  () => {
    if (
      !members.value.some((member) => member.agentId === selectedAgentId.value && member.available)
    )
      selectedAgentId.value =
        members.value.find((member) => member.available)?.agentId ?? props.task.activeAgentId
  },
  { immediate: true }
)
function setSelectedAgent(value: unknown): void {
  selectedAgentId.value = typeof value === 'string' ? value : null
}
function send(): void {
  if (canSend.value && selectedAgentId.value)
    emit('send', { agentId: selectedAgentId.value, message: message.value.trim() }, () => {
      message.value = ''
    })
}
function keydown(event: KeyboardEvent): void {
  if (
    event.key === 'Enter' &&
    !event.isComposing &&
    !event.shiftKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey
  ) {
    event.preventDefault()
    send()
  }
}
function openPicker(): void {
  if (props.locked) return
  pickerOpen.value = true
  emit('load-agents')
}
function addSelectedMembers(ids: string[]): void {
  const existing = new Set(members.value.map((member) => member.agentId))
  emit(
    'add-members',
    ids.filter((id) => !existing.has(id))
  )
}
function openEditor(): void {
  editingTitle.value = props.task.title
  editingGoal.value = props.task.goal
  editorOpen.value = true
}
function saveEditor(): void {
  if (
    props.locked ||
    !validateWorkTitle(editingTitle.value).valid ||
    !validateWorkGoal(editingGoal.value).valid
  )
    return
  emit('save', { title: editingTitle.value.trim(), goal: editingGoal.value.trim() }, () => {
    editorOpen.value = false
  })
}
</script>

<style scoped>
.work-task-page {
  min-height: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow-x: hidden;
  overflow-y: auto;
  background: var(--gray-10);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.work-task-header {
  width: min(1040px, 100%);
  min-width: 0;
  align-self: center;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  padding: 22px 24px 16px;
  border-bottom: 1px solid var(--gray-150);
}
.work-task-header {
  flex-shrink: 0;
}
.work-task-header__copy {
  min-width: 0;
  flex: 1;
}
.work-task-header__title-row {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.work-task-header h1 {
  min-width: 0;
  display: -webkit-box;
  overflow: hidden;
  margin: 0;
  color: var(--color-text);
  font-size: 18px;
  line-height: 1.45;
  overflow-wrap: anywhere;
  letter-spacing: 0;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.work-task-header p {
  display: -webkit-box;
  overflow: hidden;
  margin: 8px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.65;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.work-task-header__actions {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.work-task-header__actions :deep(.ant-btn) {
  border-color: var(--gray-200);
  border-radius: 9px;
  background: var(--bg-container);
  box-shadow: none;
  font-size: 13px;
}
.work-task-header__title-row :deep(.ant-tag) {
  margin: 3px 0 0;
  padding: 1px 8px;
  border-radius: 7px;
}
.work-task-page > :deep(.ant-alert) {
  width: min(992px, calc(100% - 32px));
  flex-shrink: 0;
  align-self: center;
  margin: 6px 0;
  border-radius: 10px;
}
.work-composer {
  width: min(880px, calc(100% - 40px));
  flex-shrink: 0;
  align-self: center;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  margin: 8px 0 24px;
  padding: 16px;
  border: 1px solid var(--gray-200);
  border-radius: 18px;
  background: var(--bg-container);
  box-shadow: 0 6px 24px rgb(0 0 0 / 4%);
}
.work-composer:focus-within {
  border-color: var(--main-400);
  box-shadow: 0 0 0 3px var(--main-50);
}
.work-composer > :deep(.ant-input) {
  grid-column: 1 / -1;
  grid-row: 1;
  padding: 2px 0;
  border: 0;
  background: transparent;
  line-height: 1.7;
  box-shadow: none;
}
.work-composer__controls {
  grid-column: 1;
  grid-row: 2;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.work-composer__controls :deep(.ant-select) {
  width: min(260px, 100%);
}
.work-composer__controls :deep(.ant-select-selector) {
  border-color: var(--gray-150);
  border-radius: 9px;
  background: var(--gray-10);
  box-shadow: none;
}
.work-composer__unavailable {
  color: var(--workspace-danger);
  font-size: 12px;
}
.work-composer__submit {
  grid-column: 2;
  grid-row: 2;
  display: flex;
  align-items: flex-end;
}
.work-composer__submit :deep(.ant-btn) {
  min-width: 82px;
  height: 36px;
  border-radius: 10px;
}
.work-info-title,
.work-info-goal {
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.work-info-title {
  margin-top: 0;
  font-size: 18px;
  letter-spacing: 0;
}
.work-info-goal {
  max-height: 55vh;
  overflow-y: auto;
  line-height: 1.7;
}
@media (max-width: 640px) {
  .work-task-header {
    padding: 16px 14px 12px;
    gap: 12px;
    flex-direction: column;
  }
  .work-task-header__actions {
    width: 100%;
    justify-content: flex-start;
  }
  .work-composer {
    width: calc(100% - 24px);
    margin-bottom: 12px;
    padding: 12px;
    border-radius: 16px;
  }
  .work-composer__controls {
    align-items: flex-start;
    flex-direction: column;
  }
  .work-task-page > :deep(.ant-alert) {
    width: calc(100% - 24px);
  }
}
</style>
