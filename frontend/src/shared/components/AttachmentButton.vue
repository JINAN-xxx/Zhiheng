<script setup lang="ts">
import { Button as AButton } from 'ant-design-vue'
import { PlusOutlined } from '@ant-design/icons-vue'

withDefaults(
  defineProps<{
    disabled?: boolean
    loading?: boolean
    label?: string
    ariaLabel?: string
    title?: string
  }>(),
  { disabled: false, loading: false, label: '添加附件', ariaLabel: '', title: '' }
)
const emit = defineEmits<{ click: [event: MouseEvent] }>()
</script>

<template>
  <AButton
    class="attachment-upload-button"
    type="text"
    :disabled="disabled"
    :loading="loading"
    :aria-label="ariaLabel || label"
    :title="title || label"
    @click="emit('click', $event)"
    ><PlusOutlined aria-hidden="true" /><span class="composer-tool-label">{{
      label
    }}</span></AButton
  >
</template>

<style scoped>
.attachment-upload-button {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
  border-radius: 9px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1;
}
.attachment-upload-button :deep(.anticon) {
  font-size: 16px;
}
.attachment-upload-button :deep(.anticon + span) {
  margin-inline-start: 0;
}
.attachment-upload-button:disabled {
  color: var(--color-text-tertiary);
}
@media (max-width: 600px) {
  .attachment-upload-button {
    width: 32px;
    padding: 0;
    gap: 0;
  }
  .composer-tool-label {
    display: none;
  }
}
</style>
