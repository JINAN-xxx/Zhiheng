<script setup lang="ts">
import HistoryDialog from '../../../shared/components/HistoryDialog.vue'
import ChatNavigation from './ChatNavigation.vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
</script>
<template>
  <HistoryDialog
    :open="open"
    title="历史记录"
    dialog-id="chat-history-dialog"
    @update:open="emit('update:open', $event)"
  >
    <ChatNavigation @navigate="emit('update:open', false)" />
  </HistoryDialog>
</template>
