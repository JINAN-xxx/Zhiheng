<template>
  <section class="knowledge-graph-page">
    <WorkspaceToolbar :title="database.name" subtitle="知识图谱">
      <template #actions>
        <a-button @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="刷新知识图谱">
          <a-button shape="circle" :loading="loading" aria-label="刷新知识图谱" @click="load">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-graph-page__content">
      <KnowledgeSectionNav
        active-view="graph"
        :graph-enabled="database.enable_knowledge_graph"
        @navigate="emit('navigate', $event)"
      />

      <div class="graph-controls">
        <a-select
          v-model:value="form.node_label"
          show-search
          option-filter-prop="label"
          :options="labelOptions"
          aria-label="图谱实体"
        />
        <label>
          深度
          <a-input-number v-model:value="form.max_depth" :min="1" :max="5" :precision="0" aria-label="图谱深度" />
        </label>
        <label>
          节点上限
          <a-input-number v-model:value="form.max_nodes" :min="1" :max="200" :precision="0" aria-label="图谱节点上限" />
        </label>
        <a-button type="primary" :loading="loading" @click="load">加载图谱</a-button>
      </div>

      <a-alert v-if="error" class="graph-alert" type="error" :message="error" show-icon />
      <a-alert
        v-if="graph.truncated"
        class="graph-alert"
        type="info"
        message="当前展示部分节点。可选择具体实体或调整深度继续探索。"
        show-icon
      />
      <div v-if="graph.nodes.length" class="graph-summary">
        当前子图：{{ graph.nodes.length }} 个节点 · {{ graph.links.length }} 个关系 · {{ graph.categories.length }} 种实体类型
      </div>
      <KnowledgeGraph v-if="graph.nodes.length" :graph="graph" />
      <WorkspaceState
        v-else
        :kind="loading ? 'loading' : 'empty'"
        :title="loading ? '正在加载图谱' : '暂无图谱节点'"
      />
    </div>
  </section>
</template>

<script setup>
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { computed, reactive, watch } from 'vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import KnowledgeGraph from './KnowledgeGraph.vue'
import KnowledgeSectionNav from './KnowledgeSectionNav.vue'

const props = defineProps({
  database: { type: Object, required: true },
  labels: { type: Array, default: () => [] },
  graph: {
    type: Object,
    default: () => ({ nodes: [], links: [], categories: [], truncated: false }),
  },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['back', 'navigate', 'load'])
const form = reactive({ node_label: '*', max_depth: 2, max_nodes: 100 })
const labelOptions = computed(() => [
  { value: '*', label: '全部实体' },
  ...props.labels.filter(label => label !== '*').map(label => ({ value: label, label })),
])

function load() {
  emit('load', {
    node_label: form.node_label,
    max_depth: form.max_depth,
    max_nodes: form.max_nodes,
  })
}

watch(() => props.database.db_id, () => {
  form.node_label = '*'
  form.max_depth = 2
  form.max_nodes = 100
})
</script>

<style scoped lang="scss">
.knowledge-graph-page {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
}

.knowledge-graph-page__content {
  min-width: 0;
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 20px 24px 28px;
}

.graph-controls {
  display: flex;
  min-height: 54px;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}

.graph-controls > :deep(.ant-select) {
  min-width: 220px;
  flex: 1;
}

.graph-controls label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.graph-controls :deep(.ant-input-number) {
  width: 78px;
}

.graph-alert {
  margin-top: 14px;
}

.graph-summary {
  margin: 16px 0 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.knowledge-graph-page :deep(.knowledge-graph__canvas) {
  height: min(66vh, 680px);
  min-height: 360px;
}

@media (max-width: 640px) {
  .knowledge-graph-page__content {
    padding: 16px;
  }

  .graph-controls {
    align-items: stretch;
  }

  .graph-controls > :deep(.ant-select) {
    width: 100%;
  }

  .graph-controls label {
    flex: 1 1 130px;
    justify-content: space-between;
  }

  .graph-controls > :deep(.ant-btn) {
    width: 100%;
  }

  .knowledge-graph-page :deep(.knowledge-graph__canvas) {
    min-height: 420px;
  }
}
</style>
