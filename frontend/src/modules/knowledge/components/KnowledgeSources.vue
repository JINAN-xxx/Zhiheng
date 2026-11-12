<script setup lang="ts">
import { onBeforeUnmount, reactive } from 'vue'
import { Alert as AAlert, Button as AButton, Modal as AModal, Spin as ASpin } from 'ant-design-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { saveBlob } from '../../files/public.ts'
import { downloadDocument, previewDocument } from '../api/index.ts'
import type { KnowledgeHit } from '../api/index.ts'
defineProps<{ sources: KnowledgeHit[] }>()
const preview = reactive({
  open: false,
  title: '',
  text: '',
  loading: false,
  error: '',
  fileId: '',
  baseId: '',
  documentId: '',
  downloading: false
})
const scope = new RequestScope(),
  downloadScope = new RequestScope()
function close(): void {
  scope.replace()
  downloadScope.replace()
  preview.open = false
  preview.downloading = false
}
async function show(hit: KnowledgeHit): Promise<void> {
  close()
  const ticket = scope.replace()
  Object.assign(preview, {
    open: true,
    title: hit.title,
    text: '',
    loading: true,
    error: '',
    fileId: hit.file_id,
    baseId: hit.knowledge_base_id,
    documentId: hit.document_id
  })
  try {
    const value = await previewDocument(hit.knowledge_base_id, hit.document_id, ticket.signal)
    if (ticket.isCurrent()) preview.text = value.text
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) preview.error = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) preview.loading = false
  }
}
async function download(): Promise<void> {
  if (preview.downloading || !preview.fileId) return
  const ticket = downloadScope.replace()
  preview.downloading = true
  preview.error = ''
  try {
    const blob = await downloadDocument(preview.baseId, preview.documentId, ticket.signal)
    if (ticket.isCurrent()) saveBlob(blob, preview.title)
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) preview.error = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) preview.downloading = false
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  downloadScope.dispose()
})
</script>
<template>
  <details v-if="sources.length" class="knowledge-sources">
    <summary>知识来源 · {{ sources.length }}</summary>
    <ol>
      <li v-for="hit in sources" :key="hit.chunk_id">
        <a-button
          type="link"
          size="small"
          :aria-label="'查看来源：' + hit.title"
          @click="show(hit)"
          >{{ hit.title }}</a-button
        >
      </li>
    </ol>
  </details>
  <a-modal
    :open="preview.open"
    :title="preview.title"
    centered
    width="min(900px, calc(100vw - 32px))"
    @cancel="close"
    ><a-spin :spinning="preview.loading"
      ><a-alert v-if="preview.error" type="error" :message="preview.error" show-icon />
      <pre v-else>{{ preview.text }}</pre></a-spin
    ><template #footer
      ><a-button @click="close">关闭</a-button
      ><a-button
        :loading="preview.downloading"
        :disabled="preview.loading || !!preview.error"
        @click="download"
        >下载资料</a-button
      ></template
    ></a-modal
  >
</template>
<style scoped>
.knowledge-sources {
  margin-top: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.knowledge-sources summary {
  cursor: pointer;
}
.knowledge-sources ol {
  margin: 8px 0;
  padding-left: 20px;
}
pre {
  max-height: 60dvh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
  color: var(--color-text);
}
</style>
