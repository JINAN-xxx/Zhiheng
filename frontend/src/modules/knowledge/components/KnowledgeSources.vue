<script setup lang="ts">
import { computed, onBeforeUnmount, reactive } from 'vue'
import { Alert as AAlert, Button as AButton, Modal as AModal, Spin as ASpin } from 'ant-design-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { saveBlob } from '../../files/public.ts'
import { downloadDocument, previewDocument } from '../api/index.ts'
import type { KnowledgeHit } from '../api/index.ts'
const props = defineProps<{ sources: KnowledgeHit[] }>()
const sourceDocuments = computed(() => {
  const documents = new Map<string, { key: string; source: KnowledgeHit; hits: KnowledgeHit[] }>()
  for (const hit of props.sources) {
    // Group by document identity, keeping the first hit's rank and each fragment's order.
    const key = JSON.stringify([hit.knowledge_base_id, hit.document_id])
    let document = documents.get(key)
    if (!document) {
      document = { key, source: hit, hits: [] }
      documents.set(key, document)
    }
    if (!document.hits.some((value) => value.chunk_id === hit.chunk_id)) document.hits.push(hit)
  }
  return [...documents.values()]
})
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
  <details v-if="sourceDocuments.length" class="knowledge-sources">
    <summary>知识来源 · {{ sourceDocuments.length }}</summary>
    <ol class="knowledge-sources__documents">
      <li
        v-for="document in sourceDocuments"
        :key="document.key"
        class="knowledge-sources__document"
      >
        <a-button
          type="link"
          size="small"
          class="knowledge-sources__title"
          :aria-label="'查看来源：' + document.source.title"
          @click="show(document.source)"
          >{{ document.source.title }}</a-button
        >
        <details class="knowledge-sources__fragments">
          <summary>命中 {{ document.hits.length }} 个片段</summary>
          <ol>
            <li v-for="(hit, index) in document.hits" :key="hit.chunk_id">
              <span class="knowledge-sources__fragment-label">片段 {{ index + 1 }}</span>
              <p>{{ hit.content }}</p>
            </li>
          </ol>
        </details>
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
.knowledge-sources__documents {
  margin: 8px 0;
  padding-left: 20px;
}
.knowledge-sources__document {
  margin-bottom: 10px;
}
.knowledge-sources__title {
  max-width: 100%;
  height: auto;
  padding: 0;
  white-space: normal;
  text-align: left;
  overflow-wrap: anywhere;
  vertical-align: top;
}
.knowledge-sources__fragments {
  margin-top: 3px;
  font-size: var(--workspace-font-size-caption);
}
.knowledge-sources__fragments > ol {
  display: grid;
  gap: 10px;
  max-height: 320px;
  margin: 8px 0 0;
  padding: 12px;
  overflow: auto;
  list-style: none;
  border: 1px solid var(--gray-150);
  border-radius: var(--workspace-radius-sm);
  background: var(--gray-10);
}
.knowledge-sources__fragment-label {
  font-weight: 500;
}
.knowledge-sources__fragments p {
  margin: 4px 0 0;
  color: var(--color-text);
  font-size: var(--workspace-font-size-helper);
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
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
