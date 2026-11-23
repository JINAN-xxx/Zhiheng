<script setup lang="ts">
import { DownloadOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import { formatFileSize, saveBlob } from '../messageFiles.ts'
import type { MessageFile, ReadFilePreview, ReadMessageFile } from '../messageFiles.ts'
import { useMessageFileRequest } from '../composables/useMessageFileRequest.ts'
import FileTypeIcon from './FileTypeIcon.vue'
import MessageFileThumbnail from './MessageFileThumbnail.vue'

const props = defineProps<{
  files: MessageFile[]
  accessKey: string | null
  readPreview: ReadFilePreview
  readFile: ReadMessageFile
  label: string
  idPrefix?: string
}>()
const emit = defineEmits<{ preview: [id: string] }>()
const { pending, error, read } = useMessageFileRequest(() => props.accessKey)
async function download(file: MessageFile): Promise<void> {
  const key = props.accessKey
  const blob = await read(file.id, (signal) => props.readFile(file.id, signal))
  if (blob && props.accessKey === key) saveBlob(blob, file.name)
}
</script>

<template>
  <section v-if="files.length" class="message-artifacts" :aria-label="label">
    <div class="message-artifacts__grid">
      <article
        v-for="item in files"
        :id="idPrefix ? `${idPrefix}${item.id}` : undefined"
        :key="item.id"
        class="artifact-card"
      >
        <button
          type="button"
          class="artifact-card__main"
          :aria-label="idPrefix ? `预览 ${item.name}` : `查看附件：${item.name}`"
          :disabled="!accessKey"
          @click="emit('preview', item.id)"
        >
          <MessageFileThumbnail
            v-if="item.image"
            :file="item"
            :access-key="accessKey"
            :preview="readPreview"
          />
          <span class="artifact-card__info">
            <FileTypeIcon :filename="item.name" :mime="item.mime" />
            <span class="artifact-card__label"
              ><strong :title="item.name">{{ item.name }}</strong
              ><span>{{ formatFileSize(item.size) }}</span></span
            >
          </span>
        </button>
        <button
          type="button"
          class="artifact-card__download"
          :aria-label="`下载 ${item.name}`"
          :title="`下载 ${item.name}`"
          :disabled="!accessKey || pending === item.id"
          @click="download(item)"
        >
          <LoadingOutlined v-if="pending === item.id" spin /><DownloadOutlined v-else />
        </button>
        <div v-if="$slots.actions" class="artifact-card__more">
          <slot name="actions" :file="item" :download="download" />
        </div>
      </article>
    </div>
    <p v-if="error" class="artifact-download-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.message-artifacts {
  min-width: 0;
  width: 100%;
  margin: 8px 0;
}
.message-artifacts__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.artifact-card {
  position: relative;
  min-width: 0;
  align-self: start;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--bg-container);
}
.artifact-card:hover {
  border-color: var(--main-color);
}
.artifact-card__main {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
  font: inherit;
}
.artifact-card__info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 13px 44px 13px 13px;
}
.artifact-card:has(.artifact-card__more) .artifact-card__info {
  padding-right: 68px;
}
.artifact-card:has(.artifact-card__more) .artifact-card__download {
  right: 36px;
}
.artifact-card__more {
  position: absolute;
  right: 7px;
  bottom: 19px;
}
.artifact-card__label {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.artifact-card__label strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
}
.artifact-card__label > span {
  font-size: 12px;
  line-height: 1.4;
  color: var(--color-text-secondary);
}
.artifact-card__download {
  position: absolute;
  right: 9px;
  bottom: 18px;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.artifact-card__download:hover {
  background: var(--main-50, #f5f3ff);
  color: var(--main-color);
}
.artifact-card__main:focus-visible,
.artifact-card__download:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -3px;
}
.artifact-download-error {
  margin: 8px 0 0;
  color: var(--workspace-danger);
  font-size: 12px;
}
@media (max-width: 560px) {
  .message-artifacts__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
