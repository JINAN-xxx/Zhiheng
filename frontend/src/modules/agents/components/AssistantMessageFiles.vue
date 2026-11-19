<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { Button as AButton, Modal as AModal, Alert as AAlert } from 'ant-design-vue'
import { DownloadOutlined } from '@ant-design/icons-vue'
import { downloadAssistantAttachment } from '../api/conversations.ts'
import type { AssistantAttachment } from '../api/conversations.ts'
import { formatFileSize, saveBlob } from '../../files/public.ts'
import { errorMessage, RequestScope } from '../../../shared/http.ts'
import { imagePreviewBlob } from '../../../shared/attachmentQueue.ts'
import FileTypeIcon from '../../../shared/components/FileTypeIcon.vue'
const props = withDefaults(
  defineProps<{
    agentId: string
    conversationId: string | null
    files: AssistantAttachment[]
    disabled?: boolean
  }>(),
  { disabled: false }
)
const scope = new RequestScope(),
  error = ref(''),
  busy = ref(''),
  opened = ref(false),
  url = ref(''),
  text = ref(''),
  name = ref(''),
  kind = ref('')
let blob: Blob | null = null
function resetPreview(): void {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
  text.value = ''
  kind.value = ''
  blob = null
  opened.value = false
}
function close(): void {
  scope.replace()
  busy.value = ''
  resetPreview()
}
async function open(file: AssistantAttachment): Promise<void> {
  if (!props.conversationId || props.disabled) return
  const context = scope.replace()
  busy.value = file.file_id
  error.value = ''
  try {
    const data = await downloadAssistantAttachment(
      props.agentId,
      props.conversationId,
      file.file_id,
      context.signal
    )
    if (!context.isCurrent()) return
    const extension = file.original_filename.split('.').at(-1)?.toLowerCase()
    const previewKind = file.mime.startsWith('image/')
      ? 'image'
      : extension === 'pdf'
        ? 'pdf'
        : ['txt', 'md', 'csv', 'json', 'html', 'htm'].includes(extension ?? '')
          ? 'text'
          : 'other'
    // Downloads deliberately use octet-stream; derive a safe preview type from validated metadata/bytes.
    const previewBlob =
      previewKind === 'image'
        ? await imagePreviewBlob(data)
        : previewKind === 'pdf'
          ? new Blob([data], { type: 'application/pdf' })
          : data
    const previewText = previewKind === 'text' ? await data.text() : ''
    if (!context.isCurrent()) return
    resetPreview()
    blob = data
    name.value = file.original_filename
    kind.value = previewKind
    text.value = previewText
    if (previewKind === 'image' || previewKind === 'pdf')
      url.value = URL.createObjectURL(previewBlob)
    opened.value = true
  } catch (reason) {
    if (context.isCurrent()) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) busy.value = ''
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  resetPreview()
})
</script>
<template>
  <div v-if="files.length" class="assistant-files">
    <AButton
      v-for="file in files"
      :key="file.file_id"
      size="small"
      :disabled="disabled || !conversationId"
      :loading="busy === file.file_id"
      :aria-label="'查看附件：' + file.original_filename"
      @click="open(file)"
    >
      <FileTypeIcon :filename="file.original_filename" :mime="file.mime" />
      <span class="file-name">{{ file.original_filename }}</span
      ><small>{{ formatFileSize(file.size) }}</small>
    </AButton>
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <AModal :open="opened" :title="name" :width="800" @cancel="close">
      <img v-if="kind === 'image'" :src="url" :alt="name" class="preview-image" />
      <iframe v-else-if="kind === 'pdf'" :src="url" :title="name" sandbox="" class="preview-pdf" />
      <pre v-else-if="kind === 'text'" class="preview-text">{{ text }}</pre>
      <p v-else>当前格式请下载后查看。</p>
      <template #footer
        ><AButton @click="close">关闭</AButton
        ><AButton type="primary" @click="blob && saveBlob(blob, name)"
          ><DownloadOutlined />下载</AButton
        ></template
      >
    </AModal>
  </div>
</template>
<style scoped>
.assistant-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}
.assistant-files :deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  height: 32px;
  border-radius: 7px;
  font-size: 12px;
}
.file-name {
  max-width: 220px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
small {
  color: var(--color-text-tertiary);
}
.preview-image {
  max-width: 100%;
  max-height: 65vh;
}
.preview-pdf {
  width: 100%;
  height: 65vh;
  border: 0;
}
.preview-text {
  white-space: pre-wrap;
  max-height: 65vh;
  overflow: auto;
  font-size: 13px;
}
</style>
