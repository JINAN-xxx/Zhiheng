<script setup lang="ts">
import { computed } from 'vue'
import { Button as AButton, Progress as AProgress, Tag as ATag } from 'ant-design-vue'
import type { KnowledgeDocument, KnowledgeJob } from '../api/index.ts'
import { formatTime, jobStatus } from '../presentation.ts'
const props = defineProps<{ jobs: KnowledgeJob[]; documents: KnowledgeDocument[]; busy: boolean }>()
const current = computed(
  () =>
    props.jobs.find(
      (job) => !['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)
    ) ?? props.jobs[0]
)
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
  <section v-if="current" class="knowledge-jobs" aria-label="索引任务">
    <article class="knowledge-job-current" aria-label="当前索引任务">
      <div class="knowledge-job__heading">
        <h3>{{ operations[current.operation] }}</h3>
        <a-tag
          :color="
            current.status === 'failed'
              ? 'error'
              : current.status === 'completed'
                ? 'success'
                : 'default'
          "
          >{{ jobStatus(current.status) }}</a-tag
        >
        <span
          >{{ current.completed_documents }}/{{ current.total_documents }} 个文档<span
            v-if="current.failed_documents"
          >
            · {{ current.failed_documents }} 个失败</span
          ></span
        >
        <a-button
          v-if="current.cancel_allowed"
          size="small"
          :disabled="busy"
          @click="emit('cancel', current)"
          >取消任务</a-button
        >
      </div>
      <a-progress
        v-if="current.total_documents > 0"
        size="small"
        :percent="Math.round((current.completed_documents / current.total_documents) * 100)"
        :status="
          current.status === 'failed'
            ? 'exception'
            : current.status === 'completed'
              ? 'success'
              : 'normal'
        "
      />
      <p v-if="current.error" class="knowledge-job__error">{{ current.error.message }}</p>
    </article>
    <details class="knowledge-job-history" aria-label="索引任务历史">
      <summary>
        索引任务历史 <span>{{ jobs.length }}</span>
      </summary>
      <article v-for="job in jobs" :key="job.id" class="knowledge-job">
        <div class="knowledge-job__heading">
          <h3>{{ operations[job.operation] }}</h3>
          <a-tag
            :color="
              job.status === 'failed' ? 'error' : job.status === 'completed' ? 'success' : 'default'
            "
            >{{ jobStatus(job.status) }}</a-tag
          >
          <a-button
            v-if="job.cancel_allowed && job.id !== current.id"
            size="small"
            :disabled="busy"
            @click="emit('cancel', job)"
            >取消任务</a-button
          >
        </div>
        <p>
          {{ job.completed_documents }}/{{ job.total_documents }} 个文档<span
            v-if="job.failed_documents"
          >
            · {{ job.failed_documents }} 个失败</span
          ><span v-if="job.chunk_count !== null"> · {{ job.chunk_count }} 个切片</span>
        </p>
        <a-progress
          v-if="job.total_documents > 0"
          size="small"
          :percent="Math.round((job.completed_documents / job.total_documents) * 100)"
          :status="
            job.status === 'failed'
              ? 'exception'
              : job.status === 'completed'
                ? 'success'
                : 'normal'
          "
        />
        <p class="knowledge-job__time">
          {{ formatTime(job.created_at)
          }}<span v-if="job.finished_at"> · 完成于 {{ formatTime(job.finished_at) }}</span>
        </p>
        <p v-if="job.error" class="knowledge-job__error">
          {{ job.error.code }} · {{ job.error.message }}
        </p>
        <details v-if="job.document_results.length" class="knowledge-job__results">
          <summary>文档结果 · {{ job.document_results.length }}</summary>
          <p
            v-for="result in job.document_results"
            :key="result.document_id"
            :class="{ 'knowledge-job__error': result.error }"
          >
            {{
              documents.find((doc) => doc.id === result.document_id)?.file_name ??
              result.document_id
            }}
            · {{ jobStatus(result.status) }} · {{ result.chunk_count ?? '—' }} 个切片<span
              v-if="result.error"
            >
              · {{ result.error.message }}</span
            >
          </p>
        </details>
        <details class="knowledge-job__identity">
          <summary>任务标识</summary>
          <p>{{ job.id }}</p>
          <p>阶段：{{ job.phase }} · 索引版本：{{ job.index_revision ?? '尚未发布' }}</p>
        </details>
      </article>
    </details>
  </section>
</template>
<style scoped>
.knowledge-jobs {
  margin-top: 18px;
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.knowledge-job-current {
  font-size: 13px;
}
.knowledge-job-history {
  margin-top: 12px;
}
summary {
  color: var(--color-text-secondary);
  font-size: 13px;
  cursor: pointer;
}
.knowledge-job-history > summary {
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.knowledge-job-history > summary span {
  margin-left: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 400;
}
.knowledge-job {
  padding: 16px 0;
  border-bottom: 1px solid var(--gray-150);
}
.knowledge-job:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}
.knowledge-job__heading {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.knowledge-job__heading :deep(.ant-btn) {
  margin-left: auto;
}
p {
  margin: 8px 0;
  overflow-wrap: anywhere;
}
.knowledge-job__time,
.knowledge-job__identity,
.knowledge-job__results {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.knowledge-job__error {
  color: var(--color-error);
}
</style>
