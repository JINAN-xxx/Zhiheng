<script setup lang="ts">
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
withDefaults(defineProps<{ value: boolean; onLabel?: string; offLabel?: string }>(), {
  onLabel: '启用',
  offLabel: '停用'
})
</script>
<template>
  <WorkspaceStatus :status="value ? 'active' : 'disabled'" :label="value ? onLabel : offLabel" />
</template>
