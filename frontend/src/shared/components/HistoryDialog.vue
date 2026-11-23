<script setup lang="ts">
import { Modal as AModal } from 'ant-design-vue'

defineProps<{ open: boolean; title: string; dialogId: string }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
</script>
<template>
  <AModal
    :open="open"
    :title="title"
    centered
    :width="620"
    :footer="null"
    :destroy-on-close="false"
    :force-render="true"
    :style="{ maxWidth: 'calc(100vw - 32px)' }"
    @cancel="emit('update:open', false)"
  >
    <div :id="dialogId" class="history-dialog-body"><slot /></div>
  </AModal>
</template>
<style scoped>
.history-dialog-body {
  display: flex;
  min-height: min(180px, 65dvh);
  max-height: min(65dvh, 600px);
  flex-direction: column;
  overflow: hidden;
}
</style>
