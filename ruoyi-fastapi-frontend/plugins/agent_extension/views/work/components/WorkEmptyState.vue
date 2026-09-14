<template>
  <section class="work-empty-state">
    <div class="work-empty-state__heading">
      <h1>今天要完成什么工作？</h1>
      <WorkspaceModeSwitch model-value="work" :can-chat="canChat" @update:model-value="emit('mode', $event)" />
    </div>

    <div v-if="canCreate" class="work-empty-state__form">
      <a-textarea
        v-model:value="goal"
        aria-label="工作目标"
        placeholder="描述目标、交付物和重要约束"
        :maxlength="32000"
        :auto-size="{ minRows: 5, maxRows: 10 }"
        :disabled="creating"
      />
      <div class="work-empty-state__members">
        <div class="work-empty-state__member-list">
          <a-tag v-for="member in selectedAgents" :key="member.agentId" closable @close="remove(member.agentId)">
            {{ member.name }} · v{{ member.publishedVersion }}
          </a-tag>
          <span v-if="!selectedAgents.length">尚未添加智能体</span>
        </div>
        <a-button :disabled="creating || selectedIds.length >= 10" @click="openPicker"><PlusOutlined />添加智能体</a-button>
      </div>
      <div class="work-empty-state__footer">
        <a-select
          v-model:value="activeAgentId"
          aria-label="当前执行者"
          placeholder="选择当前执行者"
          :options="selectedAgents.map(agent => ({ value: agent.agentId, label: agent.name }))"
          :disabled="creating || !selectedAgents.length"
        />
        <a-button type="primary" :loading="creating" :disabled="!canStart" @click="start">
          <PlayCircleOutlined />开始工作
        </a-button>
      </div>
    </div>
    <a-alert
      v-else
      type="info"
      show-icon
      message="当前账号仅可查看已有工作任务"
      description="请从最近工作或已有链接打开任务。"
    />

    <WorkAgentPicker
      v-model:open="pickerOpen"
      :agents="agents"
      :selected-ids="selectedIds"
      :loading="agentsLoading"
      :error="agentsError"
      @confirm="applySelection"
    />
  </section>
</template>

<script setup>
import { PlayCircleOutlined, PlusOutlined } from '@ant-design/icons-vue'
import WorkspaceModeSwitch from '@/components/Workspace/WorkspaceModeSwitch.vue'
import { validateWorkGoal, validateWorkMembers } from '../../../utils/workTask.js'
import WorkAgentPicker from './WorkAgentPicker.vue'

const props = defineProps({
  canChat: { type: Boolean, default: false },
  canCreate: { type: Boolean, default: false },
  agents: { type: Array, default: () => [] },
  agentsLoading: { type: Boolean, default: false },
  agentsError: { type: String, default: '' },
  creating: { type: Boolean, default: false },
  initialDraft: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['load-agents', 'mode', 'start'])
const goal = ref(props.initialDraft.goal || '')
const selectedIds = ref([...(props.initialDraft.agentIds || [])])
const activeAgentId = ref(props.initialDraft.activeAgentId || null)
const pickerOpen = ref(false)
const selectedAgents = computed(() => selectedIds.value.map((id) => props.agents.find((agent) => agent.agentId === id)).filter(Boolean))
const canStart = computed(() => validateWorkGoal(goal.value).valid
  && validateWorkMembers(selectedIds.value).valid
  && selectedIds.value.includes(activeAgentId.value))

function openPicker() {
  pickerOpen.value = true
  emit('load-agents')
}
function applySelection(ids) {
  selectedIds.value = ids
  if (!ids.includes(activeAgentId.value)) activeAgentId.value = ids[0] || null
}
function remove(agentId) {
  applySelection(selectedIds.value.filter((id) => id !== agentId))
}
function start() {
  if (!canStart.value) return
  emit('start', { goal: goal.value.trim(), agentIds: [...selectedIds.value], activeAgentId: activeAgentId.value })
}
</script>

<style scoped>
.work-empty-state { width: min(760px, calc(100% - 32px)); min-height: 100%; display: flex; flex-direction: column; justify-content: center; gap: 24px; margin: 0 auto; padding: 36px 0; font-family: var(--workspace-font-family); }
.work-empty-state__heading { display: grid; justify-items: center; gap: 22px; text-align: center; }
.work-empty-state h1 { margin: 0; color: var(--color-text); font-size: 30px; font-weight: 600; letter-spacing: 0; }
.work-empty-state__form { display: grid; gap: 14px; }
.work-empty-state__form :deep(.ant-input) { border-color: var(--gray-150); background: var(--bg-container); box-shadow: none; }
.work-empty-state__members { min-width: 0; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.work-empty-state__member-list { min-width: 0; display: flex; flex: 1; flex-wrap: wrap; gap: 6px; color: var(--color-text-secondary); font-size: 13px; }
.work-empty-state__footer { display: grid; grid-template-columns: minmax(180px, 1fr) auto; gap: 12px; }
@media (max-width: 520px) { .work-empty-state { width: min(100% - 24px, 760px); padding: 20px 0; } .work-empty-state h1 { font-size: 24px; } .work-empty-state__members { flex-direction: column; } .work-empty-state__footer { grid-template-columns: minmax(0, 1fr); } .work-empty-state__footer :deep(.ant-btn) { width: 100%; } }
</style>
