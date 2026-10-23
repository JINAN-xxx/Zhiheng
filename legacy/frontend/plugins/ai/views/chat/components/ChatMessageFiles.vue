<template>
  <div v-if="files.length" class="message-files">
    <a-button
      v-for="file in files"
      :key="`${file.kind}-${file.fileId}`"
      class="message-file"
      size="small"
      :loading="pendingId === file.fileId"
      :title="fileTitle(file)"
      :aria-label="`${file.kind === 'image' ? '预览图片' : '下载附件'}：${chatFileLabel(file, file.kind)}`"
      @click="openFile(file)"
    >
      <PictureOutlined v-if="file.kind === 'image'" /><FileTextOutlined v-else />
      <span class="message-file-name">{{ chatFileLabel(file, file.kind) }}</span>
      <span v-if="file.sizeBytes" class="message-file-size">{{ formatSize(file.sizeBytes) }}</span>
    </a-button>
    <a-modal :open="Boolean(previewUrl)" :title="previewName" :width="800" @cancel="closePreview">
      <img v-if="previewUrl" :src="previewUrl" :alt="previewName" class="file-preview" />
      <template #footer>
        <a-button @click="closePreview">关闭</a-button>
        <a-button type="primary" @click="saveAs(previewBlob, previewName)"><DownloadOutlined />下载图片</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, getCurrentInstance, onBeforeUnmount, onDeactivated, ref, shallowRef } from 'vue'
import { DownloadOutlined, FileTextOutlined, PictureOutlined } from '@ant-design/icons-vue'
import { saveAs } from 'file-saver'
import { downloadChatFile } from '../../../api/chat'
import { chatFileLabel, chatImagePreviewBlob } from '../../../utils/chatFiles.js'
import { normalizeErrorMessage } from '@/utils/errorMessage'

const props = defineProps({ images: { type: Array, default: () => [] }, attachments: { type: Array, default: () => [] } })
const { proxy } = getCurrentInstance()
const files = computed(() => [
  ...props.images.map(file => ({ ...file, kind: 'image' })),
  ...props.attachments.map(file => ({ ...file, kind: 'attachment' })),
])
const pendingId = ref(null)
const previewUrl = ref('')
const previewName = ref('')
const previewBlob = shallowRef(null)
let controller = null

function formatSize(size) { return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1024))} KB` }
function fileTitle(file) { return [chatFileLabel(file, file.kind), file.mimeType, file.sizeBytes ? formatSize(file.sizeBytes) : ''].filter(Boolean).join(' · ') }
function closePreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  previewBlob.value = null
}
async function openFile(file) {
  controller?.abort()
  const request = new AbortController()
  controller = request
  pendingId.value = file.fileId
  try {
    const blob = await downloadChatFile(file, request.signal)
    if (request.signal.aborted) return
    if (file.kind === 'image') {
      const imageBlob = await chatImagePreviewBlob(blob)
      if (request.signal.aborted) return
      closePreview()
      previewBlob.value = imageBlob
      previewName.value = chatFileLabel(file, 'image')
      previewUrl.value = URL.createObjectURL(imageBlob)
    } else saveAs(blob, chatFileLabel(file))
  } catch (error) {
    if (!request.signal.aborted) proxy.$modal.msgError(normalizeErrorMessage(error, '文件下载失败'))
  } finally {
    if (controller === request) pendingId.value = null
  }
}
function cleanup() { controller?.abort(); closePreview() }
onBeforeUnmount(cleanup)
onDeactivated(cleanup)
</script>

<style scoped>
.message-files { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.message-file { display: inline-flex; align-items: center; gap: 5px; max-width: 100%; height: auto; min-height: 30px; text-align: left; }
.message-file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: min(320px, 48vw); }
.message-file-size { color: var(--color-text-secondary); font-size: 11px; }
.file-preview { display: block; max-width: 100%; max-height: 65vh; margin: auto; object-fit: contain; }
</style>
