<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Drawer as ADrawer,
  Empty as AEmpty,
  Spin as ASpin
} from 'ant-design-vue'
import { DownloadOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons-vue'
import { MarkdownRender } from 'markstream-vue'
import { formatFileSize, saveBlob } from '../messageFiles.ts'
import type {
  FilePreviewKind,
  MessageFile,
  ReadFilePreview,
  ReadMessageFile
} from '../messageFiles.ts'
import { useMessageFileRequest } from '../composables/useMessageFileRequest.ts'

const props = defineProps<{
  file: MessageFile | null
  files: MessageFile[]
  accessKey: string | null
  readPreview: ReadFilePreview
  readFile: ReadMessageFile
}>()
const emit = defineEmits<{ close: []; select: [id: string] }>()
const access = useMessageFileRequest(() => props.accessKey)
const downloads = useMessageFileRequest(() => props.accessKey)
const kind = ref<FilePreviewKind>(''),
  url = ref(''),
  text = ref(''),
  truncated = ref(false)
const images = computed(() => props.files.filter((item) => item.image))
const imageIndex = computed(() => images.value.findIndex((item) => item.id === props.file?.id))
function release(): void {
  if (url.value) URL.revokeObjectURL(url.value)
  url.value = ''
  text.value = ''
  kind.value = ''
  truncated.value = false
}
async function load(): Promise<void> {
  release()
  const item = props.file,
    key = props.accessKey
  if (!item || !key) return
  const result = await access.read(item.id, async (signal) => {
    const preview = await props.readPreview(item.id, signal)
    if (!preview) return null
    const plain = preview.kind === 'text' || preview.kind === 'markdown'
    return {
      ...preview,
      text: plain ? await preview.blob.slice(0, 200000).text() : '',
      truncated: plain && preview.blob.size > 200000
    }
  })
  if (!result || props.accessKey !== key || props.file?.id !== item.id) return
  kind.value = result.kind
  text.value = result.text
  truncated.value = result.truncated
  if (result.kind === 'image' || result.kind === 'pdf') url.value = URL.createObjectURL(result.blob)
}
async function download(): Promise<void> {
  const item = props.file,
    key = props.accessKey
  if (!item || !key) return
  const blob = await downloads.read(item.id, (signal) => props.readFile(item.id, signal))
  if (blob && props.accessKey === key) saveBlob(blob, item.name)
}
watch(
  () => props.accessKey,
  () => {
    emit('close')
    release()
  },
  { flush: 'sync' }
)
watch(
  () => props.file?.id,
  () => {
    access.reset()
    downloads.reset()
    void load()
  },
  { immediate: true, flush: 'sync' }
)
onBeforeUnmount(release)
</script>

<template>
  <ADrawer
    :open="!!file && !!accessKey"
    :title="file?.name"
    placement="right"
    width="min(860px, 100vw)"
    :body-style="{ padding: '20px', minHeight: 0 }"
    class="message-file-preview"
    @close="emit('close')"
  >
    <template #extra
      ><AButton aria-label="下载" :loading="!!downloads.pending.value" @click="download"
        ><DownloadOutlined />下载</AButton
      ></template
    >
    <div v-if="file" class="artifact-preview" role="region" :aria-label="`预览 ${file.name}`">
      <div class="artifact-preview__meta">
        <span>{{ formatFileSize(file.size) }}{{ file.detail ? ` · ${file.detail}` : '' }}</span
        ><span
          v-if="images.length > 1 && imageIndex >= 0 && kind === 'image'"
          class="artifact-preview__paging"
          ><AButton
            size="small"
            type="text"
            aria-label="上一张图片"
            :disabled="imageIndex <= 0"
            @click="emit('select', images[imageIndex - 1]!.id)"
            ><LeftOutlined /></AButton
          >{{ imageIndex + 1 }} / {{ images.length
          }}<AButton
            size="small"
            type="text"
            aria-label="下一张图片"
            :disabled="imageIndex < 0 || imageIndex >= images.length - 1"
            @click="emit('select', images[imageIndex + 1]!.id)"
            ><RightOutlined /></AButton
        ></span>
      </div>
      <AAlert
        v-if="access.error.value || downloads.error.value"
        :message="access.error.value || downloads.error.value"
        type="error"
        show-icon
        ><template #action
          ><AButton v-if="access.error.value" size="small" @click="load">重试</AButton></template
        ></AAlert
      >
      <div v-if="access.pending.value" class="artifact-preview__loading">
        <ASpin tip="加载预览…" />
      </div>
      <img
        v-else-if="kind === 'image'"
        class="artifact-preview__image"
        :src="url"
        :alt="file.name"
      />
      <iframe
        v-else-if="kind === 'pdf'"
        class="artifact-preview__pdf"
        :src="url"
        :title="file.name"
      />
      <div v-else-if="kind === 'markdown'" class="artifact-preview__markdown">
        <MarkdownRender :content="text" html-policy="safe" :is-dark="false" :final="true" />
      </div>
      <pre v-else-if="kind === 'text'" class="artifact-preview__text">{{ text }}</pre>
      <AEmpty
        v-else-if="!access.error.value"
        :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        description="此格式暂不支持在线预览，可下载查看"
      />
      <p v-if="truncated" class="artifact-preview__notice">仅展示前 200 KB，请下载查看完整内容。</p>
    </div>
  </ADrawer>
</template>

<style scoped>
:deep(.ant-drawer-title) {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 14px;
}
.artifact-preview {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  min-width: 0;
}
.artifact-preview__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.artifact-preview__paging {
  display: flex;
  align-items: center;
  gap: 6px;
}
.artifact-preview__loading {
  display: grid;
  place-items: center;
  min-height: 180px;
}
.artifact-preview__image {
  width: 100%;
  max-height: calc(100dvh - 180px);
  object-fit: contain;
}
.artifact-preview__pdf {
  width: 100%;
  flex: 1;
  min-height: 65dvh;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.artifact-preview__text {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.8;
  font-family: var(--workspace-font-mono);
}
.artifact-preview__markdown {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.8;
}
.artifact-preview__markdown :deep(img) {
  max-width: 100%;
}
.artifact-preview__notice {
  color: var(--color-text-secondary);
  font-size: 12px;
}
</style>
