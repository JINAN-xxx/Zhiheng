<template>
  <a-modal :open="open" title="添加智能体" :confirm-loading="loading" @ok="confirm" @cancel="emit('update:open', false)">
    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" :message="error" show-icon />
      <div class="work-agent-picker">
        <label v-for="agent in agents" :key="agent.agentId" class="work-agent-picker__row">
          <a-checkbox
            :checked="draftIds.includes(agent.agentId)"
            :aria-label="agent.name"
            :disabled="!draftIds.includes(agent.agentId) && draftIds.length >= max"
            @change="toggle(agent.agentId, $event.target.checked)"
          >{{ agent.name }}</a-checkbox>
          <span>{{ agent.description || '已发布智能体' }}</span>
          <small>v{{ agent.publishedVersion }}</small>
        </label>
        <a-empty v-if="!loading && !agents.length" description="暂无已发布智能体" />
      </div>
    </a-spin>
  </a-modal>
</template>

<script setup>
const props = defineProps({
  open: { type: Boolean, default: false },
  agents: { type: Array, default: () => [] },
  selectedIds: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  max: { type: Number, default: 10 },
})
const emit = defineEmits(['confirm', 'update:open'])
const draftIds = ref([])

watch(() => props.open, (open) => {
  if (open) draftIds.value = [...props.selectedIds]
})

function toggle(agentId, checked) {
  if (checked && !draftIds.value.includes(agentId)) draftIds.value.push(agentId)
  if (!checked) draftIds.value = draftIds.value.filter((id) => id !== agentId)
}

function confirm() {
  emit('confirm', [...draftIds.value])
  emit('update:open', false)
}
</script>

<style scoped>
.work-agent-picker { display: grid; gap: 4px; margin-top: 12px; }
.work-agent-picker__row { min-width: 0; display: grid; grid-template-columns: minmax(120px, auto) minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 10px 8px; border-bottom: 1px solid var(--gray-150); }
.work-agent-picker__row span { min-width: 0; overflow: hidden; color: var(--color-text-secondary); text-overflow: ellipsis; white-space: nowrap; }
.work-agent-picker__row small { color: var(--color-text-tertiary); }
@media (max-width: 480px) { .work-agent-picker__row { grid-template-columns: minmax(0, 1fr) auto; } .work-agent-picker__row > span { grid-column: 1 / -1; white-space: normal; } }
</style>
