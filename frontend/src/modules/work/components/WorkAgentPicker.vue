<!-- Adapted from original Work UI, MIT licensed. -->
<template>
  <a-modal
    :open="open"
    title="添加智能体"
    centered
    :confirm-loading="loading"
    @ok="confirm"
    @cancel="emit('update:open', false)"
  >
    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" :message="error" show-icon />
      <div class="work-agent-picker__intro">
        <span>选择参与协作的智能体</span><span>已选 {{ draftIds.length }} / {{ max }}</span>
      </div>
      <div class="work-agent-picker workspace-scroll-area">
        <div
          v-for="agent in agents"
          :key="agent.agentId"
          class="work-agent-picker__row"
          :class="{ 'is-selected': draftIds.includes(agent.agentId) }"
        >
          <a-checkbox
            :checked="draftIds.includes(agent.agentId)"
            :aria-label="agent.name"
            :disabled="!draftIds.includes(agent.agentId) && draftIds.length >= max"
            @change="toggle(agent.agentId, $event.target?.checked ?? false)"
            >{{ agent.name }}</a-checkbox
          >
          <span>{{ agent.description || '已发布智能体' }}</span>
        </div>
        <a-empty v-if="!loading && !agents.length" description="暂无已发布智能体" />
      </div>
    </a-spin>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  Alert as AAlert,
  Checkbox as ACheckbox,
  Empty as AEmpty,
  Modal as AModal,
  Spin as ASpin
} from 'ant-design-vue'
import type { WorkAgent } from '../api/index.ts'
const props = withDefaults(
  defineProps<{
    open: boolean
    agents: WorkAgent[]
    selectedIds: string[]
    loading: boolean
    error: string
    max?: number
  }>(),
  { max: 10 }
)
const emit = defineEmits<{ confirm: [ids: string[]]; 'update:open': [open: boolean] }>()
const draftIds = ref<string[]>([])
watch(
  () => props.open,
  (open) => {
    if (open) draftIds.value = [...props.selectedIds]
  }
)
function toggle(agentId: string, checked: boolean): void {
  if (checked && !draftIds.value.includes(agentId) && draftIds.value.length < props.max)
    draftIds.value.push(agentId)
  if (!checked) draftIds.value = draftIds.value.filter((id) => id !== agentId)
}
function confirm(): void {
  if (props.loading || props.error) return
  emit('confirm', [...draftIds.value])
  emit('update:open', false)
}
</script>

<style scoped>
.work-agent-picker__intro {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin: 16px 0 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.work-agent-picker {
  display: grid;
  gap: 10px;
  max-height: min(420px, 56dvh, calc(100dvh - 200px));
  overflow: hidden auto;
  padding: 2px;
}
.work-agent-picker__row {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  gap: 7px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-10);
  transition:
    border-color 0.18s,
    background 0.18s;
}
.work-agent-picker__row.is-selected {
  border-color: var(--main-200);
  background: var(--main-50);
}
.work-agent-picker__row:focus-within {
  outline: 2px solid var(--main-400);
  outline-offset: -2px;
}
.work-agent-picker__row :deep(.ant-checkbox-wrapper) {
  min-width: 0;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.work-agent-picker__row > span {
  grid-column: 1;
  padding-left: 26px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
</style>
