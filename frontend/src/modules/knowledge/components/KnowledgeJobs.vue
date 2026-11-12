<script setup lang="ts">
import { computed } from 'vue'
import { Button as AButton, Progress as AProgress } from 'ant-design-vue'
import { ExclamationCircleOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import type { KnowledgeDocument, KnowledgeJob } from '../api/index.ts'
import { jobStatus } from '../presentation.ts'
const props = defineProps<{ jobs: KnowledgeJob[]; documents: KnowledgeDocument[]; busy: boolean }>()
const needsAttention = (job: KnowledgeJob) =>
  ['partial', 'failed', 'interrupted'].includes(job.status)
const current = computed(() => {
  const active = props.jobs.find(
    (job) => !['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)
  )
  const latest = props.jobs[0]
  return active ?? (latest && needsAttention(latest) ? latest : undefined)
})
const failed = computed(() => !!current.value && needsAttention(current.value))
const failedDocuments = computed(() => {
  const job = current.value
  if (!job) return []
  return job.document_results.flatMap((result) => {
    const document = props.documents.find(
      (doc) => doc.id === result.document_id && doc.job_id === job.id
    )
    const error = result.error ?? document?.error
    return error || result.status === 'failed' || document?.status === 'failed'
      ? [{ ...result, error: error ?? job.error }]
      : []
  })
})
const emit = defineEmits<{ cancel: [job: KnowledgeJob] }>()
const operations = {
  import: '导入文档',
  rechunk: '重新切片',
  rebuild: '重建索引',
  delete_document: '删除文档',
  delete_folder: '删除文件夹',
  delete_base: '删除知识库',
  gc: '清理索引'
}
</script>
<template>
  <section v-if="current" class="knowledge-jobs" aria-label="当前索引任务">
    <div class="knowledge-job__heading" role="status">
      <ExclamationCircleOutlined v-if="failed" class="knowledge-job__warning" />
      <LoadingOutlined v-else spin />
      <strong>{{ operations[current.operation] }}</strong>
      <span>{{ jobStatus(current.status) }}</span>
      <span v-if="current.total_documents > 0" class="knowledge-job__count">
        {{ current.completed_documents }}/{{ current.total_documents }} 个文档<span
          v-if="current.failed_documents"
        >
          · {{ current.failed_documents }} 个失败</span
        >
      </span>
      <a-progress
        v-if="!failed && current.total_documents > 0"
        class="knowledge-job__progress"
        size="small"
        :show-info="false"
        :percent="
          Math.min(100, Math.round((current.completed_documents / current.total_documents) * 100))
        "
      />
      <a-button
        v-if="current.cancel_allowed"
        type="link"
        size="small"
        :disabled="busy"
        @click="emit('cancel', current)"
        >取消任务</a-button
      >
    </div>
    <p v-if="failed && current.error" class="knowledge-job__error">{{ current.error.message }}</p>
    <details
      v-if="failed && failedDocuments.length"
      :key="current.id"
      class="knowledge-job__results"
    >
      <summary>查看失败文档（{{ failedDocuments.length }}）</summary>
      <p v-for="result in failedDocuments" :key="result.document_id">
        {{ documents.find((doc) => doc.id === result.document_id)?.file_name ?? '文档已移除' }} ·
        {{ result.error?.message || jobStatus(result.status) }}
      </p>
    </details>
  </section>
</template>
<style scoped>
.knowledge-jobs {
  margin-bottom: 16px;
  padding: 10px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.knowledge-job__heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.knowledge-job__heading strong {
  color: var(--color-text);
  font-weight: 500;
}
.knowledge-job__heading :deep(.anticon-loading) {
  color: var(--main-color);
}
.knowledge-job__progress {
  flex: 0 1 160px;
  margin: 0 0 0 auto;
}
.knowledge-job__heading :deep(.ant-btn) {
  margin-left: auto;
}
.knowledge-job__warning,
.knowledge-job__error {
  color: var(--color-error);
}
.knowledge-job__error,
.knowledge-job__results {
  margin: 6px 0 0 24px;
  overflow-wrap: anywhere;
}
.knowledge-job__results summary {
  width: fit-content;
  cursor: pointer;
}
.knowledge-job__results p {
  margin: 8px 0 0;
}
</style>
