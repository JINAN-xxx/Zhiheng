<template>
  <AgentEditorModal
    :model-value="true"
    :agent-id="agentId"
    page
    @update:model-value="close"
    @changed="saved"
    @deleted="close"
  />
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AgentEditorModal from '../components/AgentEditorModal.vue'
import type { Agent } from '../api/index.ts'
const route = useRoute(),
  router = useRouter()
const agentId = computed(() => (typeof route.params.id === 'string' ? route.params.id : ''))
function close(): void {
  void router.push('/agents')
}
function saved(agent: Agent): void {
  if (!agentId.value) void router.replace(`/agents/${encodeURIComponent(agent.agentId)}`)
}
</script>
