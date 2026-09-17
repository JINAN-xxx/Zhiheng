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
      <KnowledgeSectionNav
        active-view="query"
        @navigate="emit('navigate', $event)"
      />

      <div class="query-controls">
        <a-input
          v-model:value="queryText"
          :maxlength="8000"
          placeholder="输入需要验证的问题"
          aria-label="检索问题"
          @press-enter="submit"
        />
        <a-select v-model:value="queryTarget" aria-label="检索目标" :options="targetOptions" />
        <a-select
          v-if="queryTarget === 'graph'"
          v-model:value="queryMode"
          aria-label="检索模式"
          :options="modeOptions"
        />
        <a-button type="primary" :disabled="!queryText.trim()" @click="submit">
          <template #icon>
            <LoadingOutlined v-if="loading" spin />
            <SearchOutlined v-else />
          </template>
          {{ loading ? '重新检索' : '检索' }}
        </a-button>
      </div>

      <a-alert v-if="error" class="query-alert" type="error" :message="error" show-icon />
      <template v-if="result">
        <div class="query-metrics">
          <span>{{ result.items.length ? result.items.length + ' 条结果' : '检索回答' }}</span>
          <span>{{ queryChars }} 字符</span>
          <span>耗时 {{ elapsed }} ms</span>
          <span v-if="queryAverage != null">平均相似度 {{ (queryAverage * 100).toFixed(1) }}%</span>
        </div>
        <pre v-if="result.answer" class="query-answer">{{ result.answer }}</pre>
        <article v-for="(item, index) in result.items" :key="item.id + '-' + index" class="query-passage">
          <div class="query-source">
            <span>#{{ index + 1 }} · {{ item.source }}</span>
            <span v-if="item.score != null">相似度 {{ (item.score * 100).toFixed(1) }}%</span>
          </div>
          <p>{{ item.content }}</p>
        </article>
        <WorkspaceState
          v-if="!result.answer && !result.items.length"
          kind="empty"
          title="没有检索到相关内容"
        />
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

<script setup>
import { ArrowLeftOutlined, LoadingOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { computed, ref, watch } from 'vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import KnowledgeSectionNav from './KnowledgeSectionNav.vue'

const props = defineProps({
  database: { type: Object, required: true },
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  elapsed: { type: Number, default: 0 },
})

const emit = defineEmits(['back', 'refresh', 'navigate', 'query'])
const queryText = ref('')
const queryTarget = ref('kb')
const queryMode = ref('mix')
const modeOptions = [
  { value: 'mix', label: '混合检索' },
  { value: 'local', label: '段落检索' },
  { value: 'global', label: '关联检索' },
  { value: 'hybrid', label: '综合检索' },
  { value: 'naive', label: '基础检索' },
]
const targetOptions = computed(() => [
  { value: 'kb', label: '知识库' },
  { value: 'graph', label: '知识图谱', disabled: !props.database.enable_knowledge_graph },
])
const queryChars = computed(() => (props.result?.answer || '').length + (props.result?.items || []).reduce((count, item) => count + item.content.length, 0))
const queryAverage = computed(() => {
  const scores = (props.result?.items || []).map(item => item.score).filter(score => score != null)
  return scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null
})

function submit() {
  const text = queryText.value.trim()
  if (!text) return
  emit('query', { text, target: queryTarget.value, mode: queryMode.value })
}

watch(() => props.database.db_id, () => {
  queryText.value = ''
  queryTarget.value = 'kb'
  queryMode.value = 'mix'
})
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
