<!-- Adapted from original Work UI, MIT licensed. -->
<template>
  <section class="work-empty-state">
    <div class="work-empty-state__heading">
      <span class="work-empty-state__symbol" aria-hidden="true"><ThunderboltOutlined /></span>
      <h1>今天要完成什么工作？</h1>
      <WorkspaceModeSwitch
        model-value="work"
        :can-chat="canChat"
        @update:model-value="emit('mode', $event)"
      />
    </div>

    <div v-if="canCreate" class="work-empty-state__form">
      <span class="work-empty-state__label">工作目标</span>
      <a-textarea
        v-model:value="goal"
        aria-label="工作目标"
        placeholder="描述目标、交付物和重要约束"
        :maxlength="64000"
        :auto-size="{ minRows: 4, maxRows: 10 }"
        :disabled="creating"
      />
      <div class="work-empty-state__members">
        <div class="work-empty-state__member-list">
          <a-tag
            v-for="member in selectedAgents"
            :key="member.agentId"
            closable
            @close="remove(member.agentId)"
          >
            {{ member.name }} · v{{ member.publishedVersion }}
          </a-tag>
          <span v-if="!selectedAgents.length" class="work-empty-state__member-hint"
            ><TeamOutlined />添加智能体，开始协作</span
          >
        </div>
        <a-button
          aria-label="添加智能体"
          :disabled="creating || selectedIds.length >= 10"
          @click="openPicker"
          ><PlusOutlined />添加智能体</a-button
        >
      </div>
      <div class="work-empty-state__footer">
        <div class="work-empty-state__executor">
          <span>本轮执行</span>
          <a-select
            :value="activeAgentId ?? undefined"
            aria-label="当前执行者"
            placeholder="选择当前执行者"
            :options="selectedAgents.map((agent) => ({ value: agent.agentId, label: agent.name }))"
            :disabled="creating || !selectedAgents.length"
            @update:value="setActiveAgent"
          />
        </div>
        <a-button
          aria-label="开始工作"
          type="primary"
          :loading="creating"
          :disabled="!canStart"
          @click="start"
        >
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

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Select as ASelect,
  Tag as ATag,
  Textarea as ATextarea
} from 'ant-design-vue'
import {
  PlayCircleOutlined,
  PlusOutlined,
  TeamOutlined,
  ThunderboltOutlined
} from '@ant-design/icons-vue'
import WorkspaceModeSwitch from '../../../shared/components/WorkspaceModeSwitch.vue'
import { validateWorkGoal, validateWorkMembers } from '../presentation.ts'
import type { WorkDraft } from '../presentation.ts'
import type { WorkAgent } from '../api/index.ts'
import WorkAgentPicker from './WorkAgentPicker.vue'
const props = defineProps<{
  canChat: boolean
  canCreate: boolean
  agents: WorkAgent[]
  agentsLoading: boolean
  agentsError: string
  creating: boolean
  initialDraft: WorkDraft
}>()
const emit = defineEmits<{
  'load-agents': []
  mode: [mode: 'chat' | 'work']
  start: [draft: WorkDraft]
}>()
const goal = ref(props.initialDraft.goal)
const selectedIds = ref([...props.initialDraft.agentIds])
const activeAgentId = ref<string | null>(props.initialDraft.activeAgentId)
const pickerOpen = ref(false)
const selectedAgents = computed(() =>
  selectedIds.value
    .map((id) => props.agents.find((agent) => agent.agentId === id))
    .filter((agent): agent is WorkAgent => agent !== undefined)
)
const canStart = computed(
  () =>
    !props.creating &&
    validateWorkGoal(goal.value).valid &&
    validateWorkMembers(selectedIds.value).valid &&
    selectedAgents.value.length === selectedIds.value.length &&
    activeAgentId.value !== null &&
    selectedIds.value.includes(activeAgentId.value)
)
function setActiveAgent(value: unknown): void {
  activeAgentId.value = typeof value === 'string' ? value : null
}
function openPicker(): void {
  pickerOpen.value = true
  emit('load-agents')
}
function applySelection(ids: string[]): void {
  selectedIds.value = ids
  if (!activeAgentId.value || !ids.includes(activeAgentId.value))
    activeAgentId.value = ids[0] ?? null
}
function remove(agentId: string): void {
  if (!props.creating) applySelection(selectedIds.value.filter((id) => id !== agentId))
}
function start(): void {
  if (canStart.value)
    emit('start', {
      goal: goal.value.trim(),
      agentIds: [...selectedIds.value],
      activeAgentId: activeAgentId.value
    })
}
</script>

<style scoped>
.work-empty-state {
  width: min(800px, calc(100% - 48px));
  min-height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 28px;
  margin: 0 auto;
  padding: 36px 0;
  font-family: var(--workspace-font-family);
}
.work-empty-state__heading {
  display: grid;
  justify-items: center;
  gap: 16px;
  text-align: center;
}
.work-empty-state__symbol {
  display: grid;
  width: 48px;
  height: 48px;
  margin-bottom: 4px;
  place-items: center;
  border: 1px solid var(--main-100);
  border-radius: 12px;
  background: var(--main-50);
  color: var(--main-color);
  font-size: 24px;
}
.work-empty-state h1 {
  margin: 0;
  color: var(--color-text);
  font-size: 26px;
  font-weight: 500;
  line-height: 1.4;
  letter-spacing: normal;
}
.work-empty-state__heading p {
  margin: -4px 0 6px;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.7;
}
.work-empty-state__form {
  display: grid;
  gap: 12px;
  padding: 20px;
  border: 1px solid var(--gray-200);
  border-radius: 12px;
  background: var(--bg-container);
  box-shadow: none;
}
.work-empty-state__label {
  color: var(--color-text);
  font-size: 13px;
  font-weight: 500;
}
.work-empty-state__form:focus-within {
  border-color: var(--main-400);
  box-shadow:
    0 0 0 3px var(--main-50),
    0 6px 24px rgb(0 0 0 / 4%);
}
.work-empty-state__form :deep(.ant-input) {
  padding: 0;
  border: 0;
  background: transparent;
  line-height: 1.8;
  box-shadow: none;
}
.work-empty-state__members {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
}
.work-empty-state__members :deep(.ant-btn) {
  flex-shrink: 0;
  border-color: var(--gray-150);
  border-radius: 9px;
  box-shadow: none;
}
.work-empty-state__member-list {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.work-empty-state__member-list :deep(.ant-tag) {
  max-width: 100%;
  margin: 0;
  padding: 4px 8px;
  border-color: var(--main-100);
  border-radius: 8px;
  background: var(--main-50);
  color: var(--main-color);
  white-space: normal;
  overflow-wrap: anywhere;
}
.work-empty-state__member-hint {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--color-text-tertiary);
}
.work-empty-state__footer {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 16px;
  border-top: 1px solid var(--gray-150);
}
.work-empty-state__executor {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}
.work-empty-state__executor > span {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.work-empty-state__executor :deep(.ant-select) {
  width: 220px;
  min-width: 0;
}
.work-empty-state__executor :deep(.ant-select-selector) {
  border-color: var(--gray-150);
  border-radius: 9px;
  background: var(--gray-10);
  box-shadow: none;
}
.work-empty-state__footer > :deep(.ant-btn) {
  flex-shrink: 0;
  height: 40px;
  padding-inline: 18px;
  border-radius: 10px;
}
@media (max-width: 520px) {
  .work-empty-state {
    width: calc(100% - 28px);
    padding: 28px 0;
    gap: 24px;
  }
  .work-empty-state__heading {
    gap: 14px;
  }
  .work-empty-state__heading p {
    font-size: 13px;
  }
  .work-empty-state__symbol {
    width: 42px;
    height: 42px;
    border-radius: 14px;
    font-size: 22px;
  }
  .work-empty-state__form {
    padding: 16px;
    border-radius: 12px;
  }
  .work-empty-state__members {
    align-items: flex-start;
    flex-direction: column;
  }
  .work-empty-state__footer {
    align-items: stretch;
    flex-direction: column;
  }
  .work-empty-state__executor :deep(.ant-select) {
    flex: 1;
    width: 0;
  }
  .work-empty-state__footer > :deep(.ant-btn) {
    width: 100%;
  }
}
</style>
