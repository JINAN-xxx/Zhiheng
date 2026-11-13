<script setup lang="ts">
import { Modal as AModal } from 'ant-design-vue'
import ChatNavigation from './ChatNavigation.vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
</script>
<template>
  <AModal
    :open="open"
    title="历史记录"
    centered
    :width="620"
    :footer="null"
    :destroy-on-close="false"
    :force-render="true"
    :style="{ maxWidth: 'calc(100vw - 32px)' }"
    @cancel="emit('update:open', false)"
  >
    <div id="chat-history-dialog" class="chat-history-dialog-body">
      <ChatNavigation @navigate="emit('update:open', false)" />
    </div>
  </AModal>
</template>
<style scoped>
.chat-history-dialog-body {
  display: flex;
  min-height: min(180px, 65dvh);
  max-height: min(65dvh, 600px);
  flex-direction: column;
  overflow: hidden;
}
</style>
