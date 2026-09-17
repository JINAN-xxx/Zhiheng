<template>
  <section class="knowledge-graph-page">
    <WorkspaceToolbar :title="database.name" subtitle="知识图谱">
      <template #actions>
        <a-button @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="刷新知识图谱">
          <a-button shape="circle" :loading="loading || buildLoading" aria-label="刷新知识图谱" @click="emit('refresh')">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-graph-page__content">
      <KnowledgeSectionNav
        active-view="graph"
        @navigate="emit('navigate', $event)"
      />

      <section class="graph-build-panel" aria-label="图谱构建" aria-live="polite">
        <a-spin :spinning="buildLoading">
          <h2>{{ buildTitle }}</h2>
          <template v-if="!database.enable_knowledge_graph">
            <p v-if="canEdit">选择对话模型后，可从已有文档中提取实体与关系。构建会调用所选模型。</p>
            <p v-else>此知识库尚未启用图谱，请联系有编辑权限的成员启用。</p>
            <a-select v-if="canEdit" v-model:value="modelId" class="graph-model-select" aria-label="图谱对话模型" :options="chatOptions" show-search option-filter-prop="label" placeholder="选择已发布的对话模型" :disabled="buildLoading || starting || !available" />
            <p v-if="canEdit && available && !buildLoading && !chatOptions.length">暂无已发布的对话模型，请联系管理员配置。</p>
          </template>
          <template v-else>
            <p v-if="job.total_documents">已处理 {{ job.processed_documents }} / {{ job.total_documents }} 个文档 · 失败 {{ job.failed_documents }} 个</p>
            <p v-else>上传文档后可构建图谱，补建会处理尚未完成的文档。</p>
            <a-progress v-if="job.total_documents || building" :percent="progress" :status="['failed', 'interrupted'].includes(job.status) ? 'exception' : building ? 'active' : 'normal'" />
            <p v-if="building && job.current_document">正在处理：{{ job.current_document }}</p>
            <p v-if="building">任务将在后台继续，可切换页面查看文档或进行检索。</p>
          </template>
          <a-alert v-if="!available && !buildLoading" class="graph-alert" type="warning" message="知识图谱服务尚未就绪，请联系管理员完成配置。" show-icon />
          <a-alert v-if="job.error || buildError" class="graph-alert" type="error" :message="buildError || job.error" show-icon />
          <a-button v-if="canEdit" class="graph-build-action" type="primary" :loading="starting" :disabled="building || buildLoading || !available || (!database.enable_knowledge_graph && !modelId)" @click="emit('start', modelId)">{{ buildAction }}</a-button>
        </a-spin>
      </section>

      <template v-if="job.status === 'completed' && available">
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
      </template>
    </div>
  </section>
</template>

<script setup>
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { computed, reactive, ref, watch } from 'vue'
import { isKnowledgeGraphBuilding, knowledgeGraphProgress } from '../../../utils/knowledge.js'
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
  job: { type: Object, required: true },
  available: { type: Boolean, default: false },
  buildLoading: { type: Boolean, default: false },
  buildError: { type: String, default: '' },
  starting: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
  chatOptions: { type: Array, default: () => [] },
})

const emit = defineEmits(['back', 'navigate', 'load', 'refresh', 'start'])
const modelId = ref()
const building = computed(() => isKnowledgeGraphBuilding(props.job))
const progress = computed(() => knowledgeGraphProgress(props.job))
const buildTitle = computed(() => ({ disabled: '启用知识图谱', idle: '图谱待构建', queued: '图谱构建排队中', running: '正在构建图谱', completed: '图谱构建完成', failed: '图谱构建失败', interrupted: '图谱构建已中断' })[props.job.status])
const buildAction = computed(() => !props.database.enable_knowledge_graph ? '启用并构建图谱' : building.value ? '构建中' : ['failed', 'interrupted'].includes(props.job.status) ? '重试构建' : '补建图谱')
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
  modelId.value = undefined
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

.graph-build-panel { padding: 18px; margin-bottom: 18px; border: 1px solid var(--gray-150); border-radius: 8px; }
.graph-build-panel h2 { margin: 0 0 10px; font-size: 16px; }
.graph-build-panel p { margin: 8px 0; color: var(--color-text-secondary); }
.graph-model-select { width: min(420px, 100%); }
.graph-build-action { margin-top: 14px; }

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
