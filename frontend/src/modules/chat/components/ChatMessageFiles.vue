<!-- Adapted from original ChatMessageFiles, MIT licensed. -->
<template>
  <div v-if="files.length" class="message-files">
    <a-button
      v-for="file in files"
      :key="file.file_id"
      class="message-file"
      size="small"
      :loading="pendingId === file.file_id"
      :title="fileTitle(file)"
      :aria-label="`${file.mime.startsWith('image/') ? '预览图片' : '下载附件'}：${file.original_filename}`"
      @click="openFile(file)"
    >
      <PictureOutlined v-if="file.mime.startsWith('image/')" /><FileTextOutlined v-else />
      <span class="message-file-name">{{ file.original_filename }}</span>
      <span v-if="file.size" class="message-file-size">{{ formatFileSize(file.size) }}</span>
    </a-button>
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <a-modal :open="Boolean(previewUrl)" :title="previewName" :width="800" @cancel="closePreview">
      <img v-if="previewUrl" :src="previewUrl" :alt="previewName" class="file-preview" />
      <template #footer>
        <a-button @click="closePreview">关闭</a-button>
        <a-button type="primary" @click="savePreview()"><DownloadOutlined />下载图片</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { Alert as AAlert, Button as AButton, Modal as AModal } from 'ant-design-vue'
import { DownloadOutlined, FileTextOutlined, PictureOutlined } from '@ant-design/icons-vue'
import { downloadFile, formatFileSize, saveBlob } from '../../files/public.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { imagePreviewBlob } from '../attachments.ts'
import type { ChatAttachment } from '../api/index.ts'
defineProps<{ files: ChatAttachment[] }>()
const pendingId = ref<string | null>(null),
  previewUrl = ref(''),
  previewName = ref(''),
  error = ref('')
const previewBlob = shallowRef<Blob | null>(null),
  scope = new RequestScope()
const fileTitle = (file: ChatAttachment) =>
  `${file.original_filename} · ${file.mime} · ${formatFileSize(file.size)}`
function closePreview(): void {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  previewBlob.value = null
}
function savePreview(): void {
  if (previewBlob.value) saveBlob(previewBlob.value, previewName.value)
}
async function openFile(file: ChatAttachment): Promise<void> {
  const context = scope.replace()
  pendingId.value = file.file_id
  error.value = ''
  try {
    const blob = await downloadFile(file.file_id, context.signal)
    if (!context.isCurrent()) return
    if (file.mime.startsWith('image/')) {
      const image = await imagePreviewBlob(blob)
      if (!context.isCurrent()) return
      closePreview()
      previewBlob.value = image
      previewName.value = file.original_filename
      previewUrl.value = URL.createObjectURL(image)
    } else saveBlob(blob, file.original_filename)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) pendingId.value = null
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  closePreview()
})
</script>

<style scoped>
.message-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}
.message-file {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  height: auto;
  min-height: 30px;
  text-align: left;
}
.message-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: min(320px, 48vw);
}
.message-file-size {
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-caption);
}
.file-preview {
  display: block;
  max-width: 100%;
  max-height: 65vh;
  margin: auto;
  object-fit: contain;
}
</style>
