<script setup lang="ts">
import HistoryDialog from '../../../shared/components/HistoryDialog.vue'
import WorkNavigation from './WorkNavigation.vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
</script>
<template>
  <HistoryDialog
    :open="open"
    title="工作记录"
    dialog-id="work-history-dialog"
    @update:open="emit('update:open', $event)"
  >
    <WorkNavigation @navigate="emit('update:open', false)" />
  </HistoryDialog>
</template>
