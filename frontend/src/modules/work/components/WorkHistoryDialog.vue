<script setup lang="ts">
import { Modal as AModal } from 'ant-design-vue'
import WorkNavigation from './WorkNavigation.vue'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
</script>
<template>
  <AModal
    :open="open"
    title="工作记录"
    centered
    :width="620"
    :footer="null"
    :destroy-on-close="false"
    :force-render="true"
    :style="{ maxWidth: 'calc(100vw - 32px)' }"
    @cancel="emit('update:open', false)"
  >
    <div id="work-history-dialog" class="work-history-dialog-body">
      <WorkNavigation @navigate="emit('update:open', false)" />
    </div>
  </AModal>
</template>
<style scoped>
.work-history-dialog-body {
  display: flex;
  min-height: 180px;
  height: min(65dvh, 600px);
  max-height: min(65dvh, 600px);
  flex-direction: column;
  overflow: auto;
}
</style>
