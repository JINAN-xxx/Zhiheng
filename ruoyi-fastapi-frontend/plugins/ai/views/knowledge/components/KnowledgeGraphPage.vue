<template>
  <section class="knowledge-graph-page">
    <WorkspaceToolbar :title="database.name" subtitle="知识图谱">
      <template #actions>
        <a-button @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="刷新知识图谱">
          <a-button shape="circle" :loading="loading || buildLoading" :disabled="starting || configSaving || previewStarting" aria-label="刷新知识图谱" @click="emit('refresh')">
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

      <a-alert v-if="!available && !buildLoading" class="graph-service-alert" type="warning" message="知识图谱服务尚未就绪，请联系管理员完成配置。" show-icon />
      <a-spin :spinning="buildLoading">
        <div class="graph-setup-grid">
          <section class="graph-panel" aria-label="图谱配置">
            <h2>图谱配置</h2>
            <p>选择对话模型，从文档中提取实体与关系。试构建和正式构建均会调用所选模型。</p>
            <p v-if="!canEdit">当前为只读模式。如需调整配置或构建图谱，请联系有编辑权限的成员。</p>
            <a-form layout="vertical" :disabled="controlsDisabled">
              <a-form-item label="图谱对话模型" required>
                <a-select v-model:value="configForm.llm_model_id" aria-label="图谱对话模型" :options="modelOptions" show-search option-filter-prop="label" placeholder="选择已发布的对话模型" :disabled="controlsDisabled" />
              </a-form-item>
              <p v-if="canEdit && available && !buildLoading && !chatOptions.length">暂无已发布的对话模型，请联系管理员配置。</p>
              <a-collapse ghost class="graph-advanced">
                <a-collapse-panel key="advanced" header="高级选项">
                  <a-form-item label="推理策略">
                    <a-select v-model:value="configForm.reasoning_strategy" :options="strategyOptions" aria-label="图谱推理策略" :disabled="controlsDisabled" />
                    <p class="graph-field-hint">默认自动适配模型能力。</p>
                  </a-form-item>
                  <div class="graph-number-fields">
                    <a-form-item label="响应超时（秒）">
                      <a-input-number v-model:value="configForm.timeout_seconds" :min="30" :max="600" :precision="0" aria-label="图谱响应超时秒数" :disabled="controlsDisabled" />
                    </a-form-item>
                    <a-form-item label="最大输出长度">
                      <a-input-number v-model:value="configForm.max_output_tokens" :min="1024" :max="32768" :precision="0" aria-label="图谱最大输出长度" :disabled="controlsDisabled" />
                    </a-form-item>
                    <a-form-item label="并行任务数">
                      <a-input-number v-model:value="configForm.concurrency" :min="1" :max="4" :precision="0" aria-label="图谱并行任务数" :disabled="controlsDisabled" />
                    </a-form-item>
                  </div>
                </a-collapse-panel>
              </a-collapse>
            </a-form>
            <a-alert v-if="configError || formError" class="graph-alert" type="error" :message="formError || configError" show-icon />
            <div class="graph-actions">
              <a-button v-if="canEdit" :loading="configSaving" :disabled="controlsDisabled || !dirty || !configForm.llm_model_id" @click="saveConfig">保存配置</a-button>
              <span class="graph-muted">{{ dirty ? '配置尚未保存，构建时将自动保存。' : configState.config ? '配置已保存' : '请选择模型并保存配置' }}</span>
            </div>
          </section>

          <section class="graph-panel" aria-label="图谱试构建" aria-live="polite">
            <h2>{{ previewTitle }}</h2>
            <p>先用一个文档验证当前配置，查看实体和关系的提取结果。试构建不改变正式图谱。</p>
            <a-select v-if="canEdit" v-model:value="previewDocumentId" class="graph-preview-select" :options="documentOptions" allow-clear show-search option-filter-prop="label" aria-label="试构建文档" placeholder="自动选择一个文档" :disabled="controlsDisabled" />
            <p v-if="preview.file_name">测试文档：{{ preview.file_name }}</p>
            <p v-if="previewStage">当前阶段：{{ previewStage }}<span v-if="preview.progress.total_chunks"> · 已提取 {{ preview.progress.extracted_chunks || 0 }} / {{ preview.progress.total_chunks }} 个切片</span></p>
            <div v-if="preview.result" class="graph-preview-result">
              <span><strong>{{ preview.result.entities }}</strong> 个实体</span>
              <span><strong>{{ preview.result.relations }}</strong> 个关系</span>
              <span><strong>{{ preview.result.chunks }}</strong> 个切片</span>
              <span>耗时 <strong>{{ preview.result.seconds.toFixed(1) }}</strong> 秒</span>
            </div>
            <p v-if="previewBuilding">试构建正在后台进行，返回此页后会继续更新进度。</p>
            <a-alert v-if="previewError || preview.error" class="graph-alert" type="error" :message="previewError || preview.error" show-icon />
            <div v-if="canEdit" class="graph-actions">
              <a-button :loading="previewStarting || previewBuilding" :disabled="controlsDisabled || !configForm.llm_model_id" @click="previewGraph">{{ previewBuilding ? '试构建中' : '试构建' }}</a-button>
            </div>
          </section>
        </div>

        <section class="graph-panel graph-build-panel" aria-label="图谱构建" aria-live="polite">
          <h2>{{ buildTitle }}</h2>
          <p v-if="job.total_documents">已处理 {{ job.processed_documents }} / {{ job.total_documents }} 个文档 · 失败 {{ job.failed_documents }} 个</p>
          <p v-else>上传文档后可构建图谱，补建会处理尚未完成的文档。</p>
          <a-progress v-if="job.total_documents || building" :percent="progress" :status="['failed', 'interrupted'].includes(job.status) ? 'exception' : building ? 'active' : 'normal'" />
          <p v-if="building && job.current_document">正在处理：{{ job.current_document }}</p>
          <p v-if="buildStage">当前阶段：{{ buildStage }}<span v-if="job.progress.total_chunks"> · 已提取 {{ job.progress.extracted_chunks || 0 }} / {{ job.progress.total_chunks }} 个切片</span></p>
          <p v-if="building">任务将在后台继续，可切换页面查看文档或进行检索。</p>
          <p v-if="configState.requires_rebuild">{{ configState.can_resume ? '新版本尚未完成，可继续构建。' : '当前配置需要构建新版本，完成后自动切换。' }}</p>
          <p v-if="readable && (building || ['failed', 'interrupted'].includes(job.status))">下方仍可查看已完成的图谱版本。</p>
          <a-alert v-if="job.error || buildError" class="graph-alert" type="error" :message="buildError || job.error" show-icon />
          <div v-if="canEdit" class="graph-actions">
            <a-button type="primary" :loading="starting" :disabled="controlsDisabled || !configForm.llm_model_id" @click="startBuild()">{{ buildAction }}</a-button>
            <a-button v-if="database.enable_knowledge_graph" :disabled="controlsDisabled || !configForm.llm_model_id" @click="startBuild(true)">重新构建</a-button>
            <span v-if="database.enable_knowledge_graph" class="graph-muted">重新构建会创建新版本，完成后自动切换。</span>
          </div>
        </section>
      </a-spin>

      <template v-if="readable && available">
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
import { buildKnowledgeGraphConfigPayload, KNOWLEDGE_GRAPH_CONFIG_DEFAULTS, isKnowledgeGraphBuilding, knowledgeGraphProgress, knowledgeGraphStage } from '../../../utils/knowledge.js'
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
  configState: { type: Object, required: true },
  configReady: { type: Boolean, default: false },
  configSaving: { type: Boolean, default: false },
  configError: { type: String, default: '' },
  preview: { type: Object, required: true },
  previewStarting: { type: Boolean, default: false },
  previewError: { type: String, default: '' },
  documents: { type: Array, default: () => [] },
  readable: { type: Boolean, default: false },
})

const emit = defineEmits(['back', 'navigate', 'load', 'refresh', 'start', 'save-config', 'preview'])
const configForm = reactive({ ...KNOWLEDGE_GRAPH_CONFIG_DEFAULTS })
const formError = ref('')
const previewDocumentId = ref()
const building = computed(() => isKnowledgeGraphBuilding(props.job))
const previewBuilding = computed(() => isKnowledgeGraphBuilding(props.preview))
const controlsDisabled = computed(() => !props.canEdit || !props.available || !props.configReady || props.buildLoading || props.starting || props.configSaving || props.previewStarting || building.value || previewBuilding.value)
const dirty = computed(() => !props.configState.config || Object.keys(KNOWLEDGE_GRAPH_CONFIG_DEFAULTS).some(key => configForm[key] !== props.configState.config[key]))
const progress = computed(() => knowledgeGraphProgress(props.job))
const buildStage = computed(() => knowledgeGraphStage(props.job.progress))
const previewStage = computed(() => knowledgeGraphStage(props.preview.progress))
const buildTitle = computed(() => ({ disabled: '启用知识图谱', idle: '图谱待构建', queued: '图谱构建排队中', running: '正在构建图谱', completed: '图谱构建完成', failed: '图谱构建失败', interrupted: '图谱构建已中断' })[props.job.status])
const previewTitle = computed(() => ({ idle: '试构建', queued: '试构建排队中', running: '正在试构建', completed: '试构建完成', failed: '试构建失败', interrupted: '试构建已中断' })[props.preview.status])
const buildAction = computed(() => {
  if (building.value) return '构建中'
  if (dirty.value) return '保存并构建图谱'
  if (props.configState.requires_rebuild && !props.configState.can_resume) return '构建新版本'
  if (props.configState.can_resume && ['failed', 'interrupted'].includes(props.job.status)) return '续建图谱'
  return props.database.enable_knowledge_graph ? '补建图谱' : '启用并构建图谱'
})
const strategyOptions = [
  { value: 'auto', label: '自动适配（推荐）' }, { value: 'default', label: '模型默认' },
  { value: 'low', label: '轻量推理' }, { value: 'disabled', label: '关闭推理' },
]
const modelOptions = computed(() => {
  if (!configForm.llm_model_id || props.chatOptions.some(option => option.value === configForm.llm_model_id)) return props.chatOptions
  return [...props.chatOptions, { value: configForm.llm_model_id, label: '当前配置模型（暂不可选）', disabled: true }]
})
const documentOptions = computed(() => props.documents.filter(document => document.id).map(document => ({ value: document.id, label: document.file_name || document.name || '未命名文档' })))
const form = reactive({ node_label: '*', max_depth: 2, max_nodes: 100 })
const labelOptions = computed(() => [
  { value: '*', label: '全部实体' },
  ...props.labels.filter(label => label !== '*').map(label => ({ value: label, label })),
])

function submitConfig(event, extra = {}) {
  if (controlsDisabled.value) return
  formError.value = ''
  try {
    const config = buildKnowledgeGraphConfigPayload(configForm)
    emit(event, event === 'save-config' ? config : { config, ...extra })
  } catch (error) {
    formError.value = error.message
  }
}

function saveConfig() { submitConfig('save-config') }
function previewGraph() { submitConfig('preview', previewDocumentId.value ? { document_id: previewDocumentId.value } : {}) }
function startBuild(rebuild = false) { submitConfig('start', { rebuild }) }

function load() {
  emit('load', {
    node_label: form.node_label,
    max_depth: form.max_depth,
    max_nodes: form.max_nodes,
  })
}

watch(() => props.database.db_id, () => {
  previewDocumentId.value = undefined
  form.node_label = '*'
  form.max_depth = 2
  form.max_nodes = 100
})

watch(() => [props.database.db_id, props.configState.config], () => {
  Object.assign(configForm, KNOWLEDGE_GRAPH_CONFIG_DEFAULTS, props.configState.config || { llm_model_id: props.database.llm_model_id || '' })
  formError.value = ''
}, { immediate: true })
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

.graph-setup-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 18px; margin-bottom: 18px; }
.graph-panel { min-width: 0; padding: 18px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--bg-container); }
.graph-panel h2 { margin: 0 0 10px; font-size: 16px; }
.graph-panel p { margin: 8px 0; color: var(--color-text-secondary); }
.graph-panel :deep(.ant-form-item) { margin-bottom: 14px; }
.graph-build-panel { margin-bottom: 18px; }
.graph-preview-select { width: 100%; margin-top: 8px; }
.graph-number-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.graph-number-fields :deep(.ant-input-number) { width: 100%; }
.graph-advanced :deep(.ant-collapse-header) { padding-inline: 0; }
.graph-advanced :deep(.ant-collapse-content-box) { padding-inline: 0; }
.graph-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 14px; }
.graph-muted, .graph-field-hint { font-size: 12px; color: var(--color-text-secondary); }
.graph-preview-result { display: flex; flex-wrap: wrap; gap: 12px 20px; margin-top: 16px; color: var(--color-text-secondary); }
.graph-preview-result strong { color: var(--color-text); font-size: 18px; font-weight: 600; }
.graph-service-alert { margin-bottom: 18px; }

@media (max-width: 1100px) { .graph-setup-grid { grid-template-columns: minmax(0, 1fr); } }

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
