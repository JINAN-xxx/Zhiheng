<!-- Adapted from original PlatformModelsPanel.vue rate dialog, MIT licensed. -->
<script setup lang="ts">
import { Button as AButton, Modal as AModal, Tag as ATag } from 'ant-design-vue'
import { CreditRatesPanel } from '../../billing/public.ts'
import type { ChatModel } from '../api/index.ts'
defineProps<{ model: ChatModel | null; canRead: boolean; canWrite: boolean }>()
const emit = defineEmits<{ close: [] }>()
</script>
<template>
  <AModal
    :open="model !== null && canRead"
    title="费率配置"
    width="min(680px, 94vw)"
    destroy-on-close
    @cancel="emit('close')"
  >
    <div class="rate-context">
      <strong>{{ model?.name }}</strong>
      <ATag>{{
        model?.model_type === 'rerank'
          ? '重排序'
          : model?.model_type === 'embedding'
            ? '向量'
            : '对话'
      }}</ATag>
      <span class="muted">按积分计价</span>
    </div>
    <CreditRatesPanel
      v-if="model && canRead"
      :key="model.id"
      :model-id="model.id"
      :search-unit="model.model_type === 'rerank'"
      :can-write="canWrite"
    />
    <template #footer><AButton @click="emit('close')">关闭</AButton></template>
  </AModal>
</template>
<style scoped>
.rate-context {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0 20px;
  flex-wrap: wrap;
}
.rate-context strong {
  font-size: 16px;
  font-weight: 500;
}
</style>
