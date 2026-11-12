<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<script setup lang="ts">
import { FormItem as AFormItem, InputNumber as AInputNumber } from 'ant-design-vue'
defineProps<{ size: number | null; overlap: number | null }>()
const emit = defineEmits<{
  'update:size': [value: number | null]
  'update:overlap': [value: number | null]
}>()
function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
</script>
<template>
  <div class="chunk-fields">
    <a-form-item label="切片大小" required
      ><a-input-number
        :value="size ?? ''"
        :min="100"
        :max="4000"
        :precision="0"
        aria-label="切片大小"
        @update:value="emit('update:size', numeric($event))"
    /></a-form-item>
    <a-form-item label="重叠大小" required
      ><a-input-number
        :value="overlap ?? ''"
        :min="0"
        :max="Math.max(0, Math.min(1000, (size ?? 1000) - 1))"
        :precision="0"
        aria-label="重叠大小"
        @update:value="emit('update:overlap', numeric($event))"
    /></a-form-item>
  </div>
</template>
<style scoped>
.chunk-fields {
  display: flex;
  gap: 18px;
}
.chunk-fields > * {
  flex: 1;
  min-width: 0;
}
.chunk-fields :deep(.ant-input-number) {
  width: 100%;
}
</style>
