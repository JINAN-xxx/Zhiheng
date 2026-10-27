<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<template>
  <section class="knowledge-query">
    <WorkspaceToolbar :title="database.name" subtitle="检索测试">
      <template #actions>
        <a-button @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="清空检索结果">
          <a-button shape="circle" aria-label="清空检索结果" @click="emit('refresh')">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-query__content">
      <KnowledgeSectionNav active-view="query" @navigate="emit('navigate', $event)" />

      <div class="query-controls">
        <a-input
          v-model:value="queryText"
          :disabled="loading"
          :maxlength="8000"
          placeholder="输入需要验证的问题"
          aria-label="检索问题"
          @press-enter="submit"
        />
        <a-select
          :value="queryTarget"
          :disabled="loading"
          aria-label="检索目标"
          :options="targetOptions"
          @update:value="queryTarget = typeof $event === 'string' ? $event : 'kb'"
        />
        <a-select
          v-if="queryTarget === 'graph'"
          :value="queryMode"
          aria-label="检索模式"
          :options="modeOptions"
          @update:value="queryMode = typeof $event === 'string' ? $event : 'mix'"
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

      <a-space v-if="loading" class="query-alert"
        ><span>正在准备知识检索</span><a-button @click="emit('cancel')">取消检索</a-button></a-space
      >
      <a-button v-if="recoverable" class="query-alert" @click="emit('recover')">恢复查看</a-button>
      <a-alert v-if="error" class="query-alert" type="error" :message="error" show-icon />
      <template v-if="result">
        <div class="query-metrics">
          <span>{{ result.items.length + ' 条结果' }}</span>
          <span>{{ queryChars }} 字符</span>
          <span>耗时 {{ elapsed }} ms</span>
          <span v-if="queryAverage != null">平均相似度 {{ (queryAverage * 100).toFixed(1) }}%</span>
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
            <span v-if="item.score != null">相似度 {{ (item.score * 100).toFixed(1) }}%</span>
          </div>
          <p>{{ item.content }}</p>
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
import {
  ArrowLeftOutlined,
  LoadingOutlined,
  ReloadOutlined,
  SearchOutlined
} from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Select as ASelect,
  Space as ASpace,
  Tooltip as ATooltip
} from 'ant-design-vue'
import { computed, ref, watch } from 'vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import KnowledgeSectionNav from './KnowledgeSectionNav.vue'
import type { KnowledgeBase, KnowledgeHit } from '../api/index.ts'
const props = defineProps<{
  database: KnowledgeBase
  graphAvailable: boolean
  result: { items: KnowledgeHit[] } | null
  loading: boolean
  error: string
  elapsed: number
  recoverable: boolean
}>()
const emit = defineEmits<{
  back: []
  refresh: []
  navigate: [view: string]
  query: [text: string]
  cancel: []
  recover: []
  source: [hit: KnowledgeHit]
}>()
const queryText = ref(''),
  queryTarget = ref('kb'),
  queryMode = ref('mix')
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
const queryAverage = computed(() => {
  const scores = (props.result?.items ?? []).map((item) => item.score)
  return scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null
})
function submit(): void {
  if (queryText.value.trim() && !props.loading) emit('query', queryText.value.trim())
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
  padding: 20px 24px 28px;
}

.query-controls {
  display: grid;
  grid-template-columns: minmax(240px, 1fr) 150px 150px auto;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}

.query-controls > :deep(.ant-btn) {
  align-self: stretch;
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
  padding: 16px 2px;
  border-bottom: 1px solid var(--gray-150);
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

.query-answer {
  min-height: 100px;
  padding: 14px 0;
}

@media (max-width: 860px) {
  .query-controls {
    grid-template-columns: minmax(0, 1fr) 150px;
  }
}

@media (max-width: 600px) {
  .knowledge-query__content {
    padding: 16px;
  }

  .query-controls {
    grid-template-columns: minmax(0, 1fr);
  }

  .query-controls :deep(.ant-btn) {
    width: 100%;
  }
}
</style>
