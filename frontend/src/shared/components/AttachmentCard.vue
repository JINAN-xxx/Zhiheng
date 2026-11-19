<script setup lang="ts">
import { computed } from 'vue'
import { CloseOutlined } from '@ant-design/icons-vue'
import FileTypeIcon from './FileTypeIcon.vue'

const props = withDefaults(
  defineProps<{
    filename: string
    sizeLabel: string
    mime?: string
    previewUrl?: string
    status?: 'uploading' | 'ready' | 'failed'
    error?: string
    disabled?: boolean
    retryDisabled?: boolean
    removable?: boolean
    openable?: boolean
  }>(),
  {
    mime: '',
    previewUrl: '',
    status: 'ready',
    error: '',
    disabled: false,
    retryDisabled: false,
    removable: true,
    openable: false
  }
)
const emit = defineEmits<{ remove: []; retry: []; open: [] }>()
const description = computed(() => {
  if (props.status === 'uploading') return '上传中…'
  if (props.status === 'failed') return '上传失败'
  const type = props.filename.match(/\.([a-z0-9]{1,16})$/i)?.[1]?.toUpperCase() || '文件'
  return `${type} · ${props.sizeLabel}`
})
</script>

<template>
  <div class="attachment-card queued-file" :class="{ 'has-error': status === 'failed' }">
    <component
      :is="openable ? 'button' : 'div'"
      class="attachment-card__body"
      :type="openable ? 'button' : undefined"
      :disabled="openable ? disabled : undefined"
      :aria-label="openable ? '查看附件：' + filename : undefined"
      @click="openable && emit('open')"
    >
      <img v-if="previewUrl" :src="previewUrl" :alt="filename" class="queued-file-preview" />
      <FileTypeIcon v-else :filename="filename" :mime="mime" />
      <span class="queued-file-details">
        <span class="queued-file-name" :title="filename">{{ filename }}</span>
        <span class="queued-file-status" role="status">{{ description }}</span>
        <span v-if="error" class="queued-file-error">{{ error }}</span>
      </span>
    </component>
    <div v-if="removable || status === 'failed'" class="attachment-card__actions">
      <button
        v-if="status === 'failed'"
        type="button"
        class="attachment-card__retry"
        :disabled="disabled || retryDisabled"
        @click="emit('retry')"
      >
        重新上传
      </button>
      <button
        v-if="removable"
        type="button"
        class="attachment-card__remove"
        :aria-label="'移除附件：' + filename"
        :disabled="disabled"
        @click="emit('remove')"
      >
        <CloseOutlined aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.attachment-card {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
  box-sizing: border-box;
  width: 240px;
  max-width: 100%;
  min-height: 64px;
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-25);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.attachment-card.has-error {
  border-color: var(--ant-color-error, #ff4d4f);
}
.attachment-card__body {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
}
button.attachment-card__body {
  cursor: pointer;
}
.queued-file-preview {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 6px;
  flex: 0 0 40px;
}
.queued-file-details {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.queued-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
  line-height: 20px;
}
.queued-file-status,
.queued-file-error {
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}
.queued-file-status {
  color: var(--color-text-secondary);
}
.queued-file-error {
  color: var(--ant-color-error, #ff4d4f);
}
.attachment-card__actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
}
.attachment-card__remove,
.attachment-card__retry {
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.attachment-card__remove {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  font-size: 14px;
}
.attachment-card__retry {
  min-height: 28px;
  padding: 0 4px;
  color: var(--main-color);
  font-size: 12px;
}
.attachment-card__remove:hover,
.attachment-card__retry:hover {
  background: var(--gray-100);
}
.attachment-card button:disabled {
  color: var(--color-text-tertiary);
  cursor: not-allowed;
}
.attachment-card button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
