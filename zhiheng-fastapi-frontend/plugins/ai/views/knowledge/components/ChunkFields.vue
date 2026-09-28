<template>
  <div class="chunk-fields"><a-form-item label="切片大小" required><a-input-number :value="size" :min="100" :max="4000" :precision="0" aria-label="切片大小" @update:value="$emit('update:size', $event)" /></a-form-item><a-form-item label="重叠大小" required><a-input-number :value="overlap" :min="0" :max="Math.max(0, Math.min(1000, (size || 1000) - 1))" :precision="0" aria-label="重叠大小" @update:value="$emit('update:overlap', $event)" /></a-form-item></div>
</template>
<script setup>
defineProps({ size: Number, overlap: Number })
defineEmits(['update:size', 'update:overlap'])
</script>
<style scoped>
.chunk-fields { display: flex; gap: 18px; }
.chunk-fields > * { flex: 1; min-width: 0; }
.chunk-fields :deep(.ant-input-number) { width: 100%; }
</style>
