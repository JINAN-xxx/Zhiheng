<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<template>
  <section class="knowledge-query">
    <div class="knowledge-query__content">
      <div class="query-intro">
        <p>输入问题，查看知识库中匹配的资料片段。</p>
        <a-button type="text" size="small" aria-label="清空检索结果" @click="emit('refresh')"
          ><ClearOutlined />清空结果</a-button
        >
      </div>

      <div
        class="query-controls"
        :class="{
          'query-controls--advanced': canDiagnose && advanced,
          'query-controls--graph': queryTarget === 'graph'
        }"
      >
        <a-input
          v-model:value="queryText"
          :disabled="loading"
          :maxlength="8000"
          placeholder="输入需要验证的问题"
          aria-label="检索问题"
          @press-enter="submit"
        />
        <a-select
          v-if="canDiagnose && advanced"
          :value="queryTarget"
          :disabled="loading || recoverable"
          aria-label="检索目标"
          :options="targetOptions"
          @update:value="queryTarget = typeof $event === 'string' ? $event : 'kb'"
        />
        <a-select
          v-if="canDiagnose && advanced && queryTarget === 'graph'"
          :value="queryMode"
          :disabled="loading || recoverable"
          aria-label="检索模式"
          :options="modeOptions"
          @update:value="setMode"
        />
        <a-button
          type="primary"
          :disabled="!queryText.trim() || loading || recoverable"
          :aria-label="loading ? '检索中' : '检索'"
          @click="submit"
        >
          <template #icon>
            <LoadingOutlined v-if="loading" spin />
            <SearchOutlined v-else />
          </template>
          {{ loading ? '检索中' : '检索' }}
        </a-button>
      </div>

      <a-button
        v-if="canDiagnose"
        type="link"
        class="query-advanced-toggle"
        @click="advanced = !advanced"
        >{{ advanced ? '收起高级诊断' : '高级诊断' }}</a-button
      >
      <div v-if="canDiagnose && advanced && diagnostics" class="query-diagnostics">
        <p>
          召回：{{
            graphResult
              ? '图谱检索'
              : Object.values(diagnostics.retrieval_modes).includes('hybrid')
                ? '向量与关键词混合检索'
                : '基础向量检索'
          }}
          <template v-if="diagnostics.candidate_count != null">
            · 候选 {{ diagnostics.candidate_count }} 条</template
          ><template v-else> · 候选数未记录</template>
        </p>
        <p>
          重排：{{
            {
              not_configured: '未配置',
              pending: '等待中',
              completed: '已完成',
              failed: '重排失败，未返回检索结果',
              fallback: '临时降级，沿用召回顺序',
              no_candidates: '无候选，未重排'
            }[diagnostics.rerank_status]
          }}<span v-if="diagnostics.rerank_milliseconds != null">
            · {{ diagnostics.rerank_milliseconds }} ms</span
          >
        </p>
        <p v-if="diagnostics.rerank_reason">{{ diagnostics.rerank_reason }}</p>
      </div>
      <a-space v-if="loading" class="query-alert"
        ><span>正在准备知识检索</span><a-button @click="emit('cancel')">取消检索</a-button></a-space
      >
      <a-button v-if="recoverable" class="query-alert" @click="emit('recover')">恢复查看</a-button>
      <a-alert v-if="error" class="query-alert" type="error" :message="error" show-icon />
      <template v-if="graphResult">
        <pre class="query-answer">{{ graphResult.context }}</pre>
        <article v-for="source in graphResult.sources" :key="source.chunk_id" class="query-passage">
          <a-button
            type="link"
            :aria-label="'查看图谱来源：' + source.document_id"
            @click="emit('graph-source', source)"
            >查看来源</a-button
          >
          <p>{{ source.content }}</p>
          <details v-if="canDiagnose && advanced" class="query-source-details">
            <summary>完整来源标识</summary>
            <dl>
              <dt>文档</dt>
              <dd>{{ source.document_id }}</dd>
              <dt>原文文件</dt>
              <dd>{{ source.file_id }}</dd>
              <dt>切片</dt>
              <dd>{{ source.chunk_id }}</dd>
              <dt>顺序</dt>
              <dd>{{ source.ordinal }}</dd>
              <dt>内容校验</dt>
              <dd>{{ source.content_hash }}</dd>
            </dl>
          </details>
        </article>
      </template>
      <template v-else-if="result">
        <div class="query-metrics">
          <span>{{ result.items.length + ' 条结果' }}</span>
          <span>{{ queryChars }} 字符</span>
          <span>耗时 {{ elapsed }} ms</span>
        </div>

        <article v-for="(item, index) in result.items" :key="item.chunk_id" class="query-passage">
          <div class="query-source">
            <span
              >#{{ index + 1 }} ·
              <a-button
                type="link"
                size="small"
                :aria-label="'查看来源：' + item.title"
                @click="emit('source', item)"
                >{{ item.title }}</a-button
              ></span
            >
          </div>
          <p>{{ item.content }}</p>
          <details v-if="canDiagnose && advanced" class="query-source-details">
            <summary>完整来源标识</summary>
            <dl>
              <dt>知识库</dt>
              <dd>{{ item.knowledge_base_id }}</dd>
              <dt>文档</dt>
              <dd>{{ item.document_id }}</dd>
              <dt>原文文件</dt>
              <dd>{{ item.file_id }}</dd>
              <dt>切片</dt>
              <dd>{{ item.chunk_id }}</dd>
              <template v-if="item.vector_score != null"
                ><dt>向量相似度</dt>
                <dd>{{ item.vector_score.toFixed(4) }}</dd></template
              >
              <template v-if="item.fusion_score != null"
                ><dt>融合分数</dt>
                <dd>{{ item.fusion_score.toFixed(4) }}</dd></template
              >
              <template v-if="item.rerank_score != null"
                ><dt>重排分数</dt>
                <dd>{{ item.rerank_score.toFixed(4) }}</dd></template
              >
              <dt>索引版本</dt>
              <dd>{{ item.index_revision }}</dd>
            </dl>
          </details>
        </article>
        <WorkspaceState v-if="!result.items.length" kind="empty" title="没有检索到相关内容" />
      </template>
      <WorkspaceState
        v-else-if="!loading && !error"
        kind="empty"
        title="验证知识检索效果"
        message="输入问题，查看命中的文档片段、相关性与响应耗时"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { LoadingOutlined, ClearOutlined, SearchOutlined } from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Select as ASelect,
  Space as ASpace
} from 'ant-design-vue'
import { computed, ref, watch } from 'vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { identity } from '../../identity/public.ts'
import type { KnowledgeBase, KnowledgeHit, RetrievalDiagnostics } from '../api/index.ts'
import type { GraphQueryResult, GraphSource } from '../api/graph.ts'
import type { GraphQueryMode } from '../composables/useGraphQuery.ts'
const props = defineProps<{
  database: KnowledgeBase
  graphResult?: GraphQueryResult | null
  restoredGraph?: boolean
  restoredMode?: GraphQueryMode
  graphAvailable: boolean
  result: { items: KnowledgeHit[]; diagnostics?: RetrievalDiagnostics | null | undefined } | null
  loading: boolean
  error: string
  elapsed: number
  recoverable: boolean
}>()
const emit = defineEmits<{
  back: []
  refresh: []
  navigate: [view: string]
  query: [text: string, target: 'kb' | 'graph', mode: GraphQueryMode]
  'graph-source': [source: GraphSource]
  cancel: []
  recover: []
  source: [hit: KnowledgeHit]
}>()
const canDiagnose = computed(
  () => props.database.can_manage && props.database.owner_id === identity.value?.id
)
const diagnostics = computed(
  () => props.graphResult?.diagnostics ?? props.result?.diagnostics ?? null
)
const advanced = ref(false)
const queryText = ref(''),
  queryTarget = ref('kb'),
  queryMode = ref<GraphQueryMode>('mix')
watch(
  () => [props.restoredGraph, props.restoredMode],
  () => {
    if (props.restoredGraph) {
      queryTarget.value = 'graph'
      queryMode.value = props.restoredMode ?? 'mix'
    }
  },
  { immediate: true }
)
const modeOptions = [
  { value: 'mix', label: '混合检索' },
  { value: 'local', label: '段落检索' },
  { value: 'global', label: '关联检索' },
  { value: 'hybrid', label: '综合检索' },
  { value: 'naive', label: '基础检索' }
]
const targetOptions = computed(() => [
  { value: 'kb', label: '知识库' },
  {
    value: 'graph',
    label: '知识图谱',
    disabled: !props.graphAvailable || !props.database.enable_knowledge_graph
  }
])
const queryChars = computed(() =>
  (props.result?.items ?? []).reduce((count, item) => count + item.content.length, 0)
)

function setMode(value: unknown): void {
  if (
    value === 'mix' ||
    value === 'local' ||
    value === 'global' ||
    value === 'hybrid' ||
    value === 'naive'
  )
    queryMode.value = value
}
function submit(): void {
  if (queryText.value.trim() && !props.loading)
    emit(
      'query',
      queryText.value.trim(),
      queryTarget.value === 'graph' ? 'graph' : 'kb',
      queryMode.value
    )
}
watch(
  () => props.database.id,
  () => {
    queryText.value = ''
    queryTarget.value = 'kb'
    queryMode.value = 'mix'
  }
)
</script>
<style scoped lang="scss">
.knowledge-query {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
}

.knowledge-query__content {
  min-width: 0;
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 20px 0 28px;
}

.query-intro {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.query-intro p {
  margin: 0;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.query-intro :deep(.ant-btn) {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.query-controls {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) auto;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}

.query-controls--advanced {
  grid-template-columns: minmax(240px, 1fr) 150px auto;
}
.query-controls--graph {
  grid-template-columns: minmax(240px, 1fr) 150px 150px auto;
}
.query-controls > :deep(.ant-btn) {
  align-self: stretch;
}

.query-diagnostics {
  margin-top: 12px;
  padding: 12px 16px;
  background: var(--gray-50);
  font-size: 13px;
  color: var(--color-text-secondary);
}
.query-advanced-toggle {
  margin-top: 8px;
}
.query-alert {
  margin-top: 14px;
}

.query-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
  margin: 18px 0 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.query-passage {
  margin-top: 14px;
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}

.query-source {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.query-passage p,
.query-answer {
  margin: 10px 0 0;
  color: var(--color-text);
  font: inherit;
  line-height: 1.8;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.query-source-details {
  margin-top: 14px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.query-source-details summary {
  cursor: pointer;
}
.query-source-details dl {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr);
  gap: 8px;
  margin: 12px 0 0;
}
.query-source-details dt,
.query-source-details dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.query-answer {
  min-height: 100px;
  padding: 14px 0;
}

@media (max-width: 860px) {
  .query-controls,
  .query-controls--graph {
    grid-template-columns: minmax(0, 1fr) 150px;
  }
}

@media (max-width: 600px) {
  .knowledge-query__content {
    padding: 16px 0;
  }

  .query-controls,
  .query-controls--graph {
    grid-template-columns: minmax(0, 1fr);
  }

  .query-controls :deep(.ant-btn) {
    width: 100%;
  }
}
</style>
