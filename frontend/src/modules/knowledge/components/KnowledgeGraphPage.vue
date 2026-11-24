<!-- Adapted from original knowledge graph page, MIT licensed. -->
<template>
  <section
    id="knowledge-graph-panel"
    class="knowledge-graph-page"
    role="tabpanel"
    aria-label="知识图谱"
  >
    <div class="knowledge-graph-page__content">
      <a-alert
        v-if="error && !managementOpen"
        class="graph-service-alert"
        type="error"
        :message="error"
        show-icon
      />
      <a-alert
        v-if="!available && !buildLoading"
        class="graph-service-alert"
        type="warning"
        message="知识图谱服务尚未就绪，请联系管理员完成配置。"
        show-icon
      />
      <a-alert
        v-if="uncertain && !managementOpen"
        class="graph-service-alert"
        type="warning"
        message="提交结果尚未确认；确认会使用原请求标识。"
        show-icon
      />
      <a-button v-if="!managementOpen && (uncertain || recoverable)" @click="emit('recover')"
        >确认原请求或恢复查看</a-button
      >
      <section v-if="available" class="graph-canvas-section" aria-label="图谱浏览">
        <div class="graph-canvas-section__header">
          <div class="graph-canvas-section__title">
            <div class="graph-canvas-section__heading">
              <h2>图谱浏览</h2>
              <a-tag v-if="usingPreview && !readable" color="cyan">抽取预览</a-tag>
            </div>
            <div v-if="displayGraph.nodes.length" class="graph-summary">
              {{ displayGraph.nodes.length }} 个节点 · {{ displayGraph.links.length }} 个关系
              <span v-if="displayGraph.truncated"> · 部分节点</span>
            </div>
          </div>
          <div class="graph-canvas-section__actions">
            <a-radio-group
              v-if="readable && previewResult?.graph"
              v-model:value="canvasMode"
              aria-label="图谱展示模式"
            >
              <a-radio-button value="published">正式图谱</a-radio-button>
              <a-radio-button value="preview">抽取预览</a-radio-button>
            </a-radio-group>
            <a-button v-if="canEdit" aria-label="构建管理" @click="managementOpen = true">
              <template #icon><SettingOutlined /></template>
              构建管理
            </a-button>
          </div>
        </div>
        <a-alert
          v-if="taskNotice && !managementOpen"
          class="graph-task-notice"
          :type="taskNotice.type"
          :message="taskNotice.title"
          :description="taskNotice.detail"
          show-icon
        />
        <form v-if="readable && !usingPreview" class="graph-controls" @submit.prevent="load">
          <a-select
            :value="form.node_label"
            show-search
            option-filter-prop="label"
            :options="labelOptions"
            aria-label="图谱实体"
            @update:value="form.node_label = typeof $event === 'string' ? $event : '*'"
          />
          <label>
            深度
            <a-input-number
              :value="form.max_depth"
              :min="1"
              :max="5"
              :precision="0"
              aria-label="图谱深度"
              @update:value="form.max_depth = typeof $event === 'number' ? $event : 1"
            />
          </label>
          <label>
            节点上限
            <a-input-number
              :value="form.max_nodes"
              :min="1"
              :max="200"
              :precision="0"
              aria-label="图谱节点上限"
              @update:value="form.max_nodes = typeof $event === 'number' ? $event : 1"
            />
          </label>
          <a-button html-type="submit" :loading="loading">加载图谱</a-button>
        </form>
        <KnowledgeGraph v-if="displayGraph.nodes.length" :graph="displayGraph" />
        <div v-else class="graph-empty">
          <WorkspaceState
            :kind="loading || buildLoading ? 'loading' : 'empty'"
            :title="
              loading || buildLoading
                ? '正在加载图谱'
                : readable
                  ? '暂无图谱节点'
                  : '尚无可展示的图谱'
            "
            :message="
              !readable && !loading && !buildLoading && previewResult && !previewResult.graph
                ? '重新试构建可查看节点和关系。'
                : ''
            "
          />
        </div>
      </section>

      <a-drawer
        :open="managementOpen"
        title="构建管理"
        :width="'min(100%, 520px)'"
        @close="managementOpen = false"
      >
        <template #extra>
          <RefreshButton
            label="刷新任务"
            :loading="buildLoading"
            :disabled="starting || configSaving"
            @click="emit('refresh')"
          />
        </template>
        <a-alert v-if="error" class="graph-service-alert" type="error" :message="error" show-icon />
        <a-button
          v-if="uncertain || recoverable"
          class="graph-service-alert"
          @click="emit('recover')"
          >确认原请求或恢复查看</a-button
        >
        <a-spin :spinning="buildLoading">
          <section class="graph-panel" aria-label="图谱配置">
            <h2>图谱配置</h2>
            <a-form layout="vertical" :disabled="controlsDisabled">
              <a-form-item label="图谱对话模型" required>
                <a-select
                  :value="configForm.llm_model_id"
                  aria-label="图谱对话模型"
                  :options="modelOptions"
                  show-search
                  option-filter-prop="label"
                  placeholder="选择已启用的对话模型"
                  :disabled="controlsDisabled"
                  @update:value="setGraphModel"
                />
              </a-form-item>
            </a-form>
            <p v-if="canEdit && available && !buildLoading && !chatOptions.length">
              暂无已启用的对话模型，请联系管理员配置。
            </p>
            <a-collapse ghost class="graph-advanced">
              <a-collapse-panel key="advanced" header="高级选项">
                <a-form layout="vertical" :disabled="controlsDisabled">
                  <a-form-item label="推理策略">
                    <a-select
                      :value="configForm.reasoning_strategy"
                      :options="strategyOptions"
                      aria-label="图谱推理策略"
                      :disabled="controlsDisabled"
                      @update:value="setStrategy"
                    />
                    <p class="graph-field-hint">模型未声明推理参数能力时，额外推理策略不可选。</p>
                  </a-form-item>
                  <div class="graph-number-fields">
                    <a-form-item label="单次模型调用超时（秒）">
                      <a-input-number
                        :value="configForm.timeout_seconds"
                        :min="30"
                        :max="600"
                        :precision="0"
                        aria-label="图谱响应超时秒数"
                        :disabled="controlsDisabled"
                        @update:value="
                          configForm.timeout_seconds = typeof $event === 'number' ? $event : 0
                        "
                      />
                    </a-form-item>
                    <a-form-item label="最大输出长度">
                      <a-input-number
                        :value="configForm.max_output_tokens"
                        :min="1024"
                        :max="32768"
                        :precision="0"
                        aria-label="图谱最大输出长度"
                        :disabled="controlsDisabled"
                        @update:value="
                          configForm.max_output_tokens = typeof $event === 'number' ? $event : 0
                        "
                      />
                    </a-form-item>
                    <a-form-item label="并行任务数">
                      <a-input-number
                        :value="configForm.concurrency"
                        :min="1"
                        :max="4"
                        :precision="0"
                        aria-label="图谱并行任务数"
                        :disabled="controlsDisabled"
                        @update:value="
                          configForm.concurrency = typeof $event === 'number' ? $event : 0
                        "
                      />
                    </a-form-item>
                  </div>
                </a-form>
                <section class="graph-preview" aria-label="图谱试构建" aria-live="polite">
                  <h3>{{ previewTitle }}</h3>
                  <p>抽取所选文档的一段内容验证配置，试构建不改变正式图谱。</p>
                  <a-select
                    v-if="canEdit"
                    :value="previewDocumentId"
                    class="graph-preview-select"
                    :options="documentOptions"
                    allow-clear
                    show-search
                    option-filter-prop="label"
                    aria-label="试构建文档"
                    placeholder="选择预览文档（可选）"
                    :disabled="controlsDisabled"
                    @update:value="
                      previewDocumentId = typeof $event === 'string' ? $event : undefined
                    "
                  />
                  <p v-if="previewBuilding && preview?.progress.total_chunks">
                    已提取 {{ preview.progress.extracted_chunks ?? '—' }} /
                    {{ preview.progress.total_chunks }} 个切片
                  </p>
                  <div v-if="previewResult" class="graph-preview-result">
                    <span
                      ><strong>{{ previewResult.entities }}</strong> 个实体</span
                    >
                    <span
                      ><strong>{{ previewResult.relations }}</strong> 个关系</span
                    >
                    <span
                      ><strong>{{ previewResult.chunks }}</strong> 个切片</span
                    >
                    <span
                      >耗时 <strong>{{ previewResult.seconds.toFixed(1) }}</strong> 秒</span
                    >
                  </div>
                  <a-alert
                    v-if="preview?.error?.message"
                    class="graph-alert"
                    type="error"
                    :message="preview?.error?.message"
                    show-icon
                  />
                  <div v-if="canEdit" class="graph-actions">
                    <a-button
                      :loading="starting || previewBuilding"
                      :disabled="controlsDisabled || !configForm.llm_model_id"
                      @click="previewGraph"
                      >{{ previewBuilding ? '试构建中' : '试构建' }}</a-button
                    >
                    <a-button
                      v-if="preview?.cancel_allowed"
                      :loading="stopping"
                      @click="emit('cancel', preview)"
                      >取消试构建</a-button
                    >
                  </div>
                </section>
              </a-collapse-panel>
            </a-collapse>
            <a-alert
              v-if="formError"
              class="graph-alert"
              type="error"
              :message="formError"
              show-icon
            />
            <div class="graph-actions">
              <a-button
                v-if="canEdit"
                :loading="configSaving"
                :disabled="controlsDisabled || !dirty || !configForm.llm_model_id"
                @click="saveConfig"
                >保存配置</a-button
              >
              <span class="graph-muted">{{
                dirty
                  ? '配置尚未保存，构建时将自动保存。'
                  : configState?.config
                    ? '配置已保存'
                    : '请选择模型并保存配置'
              }}</span>
            </div>
          </section>

          <section class="graph-panel graph-build-panel" aria-label="图谱构建" aria-live="polite">
            <h2>{{ buildTitle }}</h2>
            <p v-if="job?.total_documents">
              已处理 {{ job?.processed_documents ?? '—' }} / {{ job?.total_documents }} 个文档
            </p>
            <p v-else>构建范围：本知识库所有已入库文档。</p>
            <a-progress
              v-if="progress !== undefined"
              v-bind="progress === undefined ? {} : { percent: progress }"
              :status="
                ['failed', 'interrupted'].includes(job?.status ?? '')
                  ? 'exception'
                  : building
                    ? 'active'
                    : 'normal'
              "
            />
            <p v-if="building && progress === undefined" class="graph-muted">进度尚未确认</p>
            <p v-if="building && job?.progress.total_chunks">
              已提取 {{ job.progress.extracted_chunks ?? '—' }} /
              {{ job.progress.total_chunks }} 个切片
            </p>
            <p v-if="configState?.requires_rebuild">当前配置需要构建新版本，完成后自动切换。</p>
            <a-alert
              v-if="job?.error?.message"
              class="graph-alert"
              type="error"
              :message="job.error.message"
              show-icon
            />
            <div v-if="canEdit" class="graph-actions">
              <a-button
                type="primary"
                :loading="starting"
                :disabled="controlsDisabled || !configForm.llm_model_id"
                @click="startBuild()"
                >{{ buildAction }}</a-button
              >
              <a-button
                v-if="database.enable_knowledge_graph"
                :disabled="controlsDisabled || !configForm.llm_model_id"
                @click="startBuild(true)"
                >重新构建</a-button
              >
              <a-button v-if="job?.cancel_allowed" :loading="stopping" @click="emit('cancel', job)"
                >取消图谱任务</a-button
              >
            </div>
          </section>
        </a-spin>
      </a-drawer>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { SettingOutlined } from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Collapse as ACollapse,
  CollapsePanel as ACollapsePanel,
  Drawer as ADrawer,
  Form as AForm,
  FormItem as AFormItem,
  InputNumber as AInputNumber,
  Progress as AProgress,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  Select as ASelect,
  Spin as ASpin,
  Tag as ATag
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import KnowledgeGraph from './KnowledgeGraph.vue'
import { chartGraph, graphIsActive, graphProgress, graphStatusLabel } from '../graphPresentation.ts'
import type { ChartGraph } from '../graphPresentation.ts'
import type { KnowledgeBase, KnowledgeDocument } from '../api/index.ts'
import type {
  GraphConfigInput,
  GraphConfigState,
  GraphJob,
  GraphPreviewResult
} from '../api/graph.ts'
const props = defineProps<{
  database: KnowledgeBase
  labels: string[]
  graph: ChartGraph
  loading: boolean
  error: string
  jobs?: GraphJob[]
  job: GraphJob | null
  available: boolean
  buildLoading: boolean
  starting: boolean
  canEdit: boolean
  chatOptions: { value: string; label: string }[]
  configState: GraphConfigState | null
  configReady: boolean
  configSaving: boolean
  documents: KnowledgeDocument[]
  preview: GraphJob | null
  readable: boolean
  uncertain: boolean
  recoverable: boolean
  stopping: boolean
}>()
const emit = defineEmits<{
  load: [value: { node_label: string; max_depth: number; max_nodes: number }]
  refresh: []
  start: [value: { config: GraphConfigInput; rebuild: boolean }]
  'save-config': [config: GraphConfigInput]
  preview: [value: { config: GraphConfigInput; document_id: string | null }]
  cancel: [job: GraphJob]
  recover: []
}>()
const defaults: GraphConfigInput = {
  llm_model_id: '',
  reasoning_strategy: 'auto',
  timeout_seconds: 240,
  max_output_tokens: 16384,
  concurrency: 2
}
const configForm = reactive({ ...defaults }),
  formError = ref(''),
  previewDocumentId = ref<string>()
const managementOpen = ref(false)
const building = computed(() => graphIsActive(props.job)),
  previewBuilding = computed(() => graphIsActive(props.preview))
const controlsDisabled = computed(
  () =>
    !props.canEdit ||
    !props.available ||
    !props.configReady ||
    props.buildLoading ||
    props.starting ||
    props.configSaving ||
    building.value ||
    previewBuilding.value ||
    props.uncertain
)
const dirty = computed(
  () =>
    !props.configState?.config ||
    (Object.keys(defaults) as (keyof GraphConfigInput)[]).some(
      (key) => configForm[key] !== props.configState?.config?.[key]
    )
)
const progress = computed(() => graphProgress(props.job))
const buildTitle = computed(() => graphStatusLabel(props.job)),
  previewTitle = computed(() => graphStatusLabel(props.preview, true))
const taskNotice = computed(() => {
  const task = building.value
    ? props.job
    : previewBuilding.value
      ? props.preview
      : props.jobs?.find(({ operation }) => operation === 'build' || operation === 'preview')
  if (!task) return null
  const title = graphStatusLabel(task, task.operation === 'preview')
  if (task.error) return { title, detail: task.error.message, type: 'error' as const }
  if (!graphIsActive(task)) return null
  const total = task.progress.total_chunks
  const processed = task.progress.extracted_chunks
  const detail = total && processed !== null ? `已提取 ${processed} / ${total} 个切片` : ''
  return { title, detail, type: 'info' as const }
})
const previewResult = computed<GraphPreviewResult | null>(() =>
  props.preview?.result && 'seconds' in props.preview.result ? props.preview.result : null
)
const canvasMode = ref<'published' | 'preview'>('published')
const usingPreview = computed(
  () => !!previewResult.value?.graph && (!props.readable || canvasMode.value === 'preview')
)
const displayGraph = computed(() =>
  usingPreview.value ? chartGraph(previewResult.value?.graph ?? null) : props.graph
)
const buildAction = computed(() =>
  building.value ? '构建中' : dirty.value ? '保存并构建图谱' : '构建图谱'
)
const strategyOptions = computed(() =>
  [
    { value: 'auto', label: '自动适配（推荐）' },
    { value: 'default', label: '模型默认' },
    { value: 'low', label: '轻量推理' },
    { value: 'disabled', label: '关闭推理' }
  ].map((option) => ({
    ...option,
    disabled:
      (option.value === 'low' &&
        configForm.llm_model_id !== props.configState?.config?.llm_model_id) ||
      !props.configState?.supported_reasoning_strategies.some((value) => value === option.value)
  }))
)

const modelOptions = computed(() =>
  !configForm.llm_model_id ||
  props.chatOptions.some((option) => option.value === configForm.llm_model_id)
    ? props.chatOptions
    : [
        ...props.chatOptions,
        { value: configForm.llm_model_id, label: '当前配置模型（暂不可选）', disabled: true }
      ]
)
const documentOptions = computed(() =>
  props.documents.map((document) => ({ value: document.id, label: document.file_name }))
)
const form = reactive({ node_label: '*', max_depth: 2, max_nodes: 100 })
const labelOptions = computed(() => [
  { value: '*', label: '全部实体' },
  ...props.labels.filter((label) => label !== '*').map((label) => ({ value: label, label }))
])
function currentConfig(): GraphConfigInput | null {
  formError.value = ''
  if (controlsDisabled.value) return null
  if (
    !configForm.llm_model_id ||
    !Number.isInteger(configForm.timeout_seconds) ||
    configForm.timeout_seconds < 30 ||
    configForm.timeout_seconds > 600 ||
    !Number.isInteger(configForm.max_output_tokens) ||
    configForm.max_output_tokens < 1024 ||
    configForm.max_output_tokens > 32768 ||
    !Number.isInteger(configForm.concurrency) ||
    configForm.concurrency < 1 ||
    configForm.concurrency > 4
  ) {
    formError.value = '请填写有效的模型、超时、输出长度和并行任务数'
    return null
  }
  return { ...configForm }
}
function setGraphModel(value: unknown): void {
  configForm.llm_model_id = typeof value === 'string' ? value : ''
  if (
    configForm.reasoning_strategy === 'low' &&
    configForm.llm_model_id !== props.configState?.config?.llm_model_id
  )
    configForm.reasoning_strategy = 'auto'
}
function setStrategy(value: unknown): void {
  if (value === 'auto' || value === 'default' || value === 'low' || value === 'disabled')
    configForm.reasoning_strategy = value
}
function saveConfig(): void {
  const value = currentConfig()
  if (value) emit('save-config', value)
}
function previewGraph(): void {
  const value = currentConfig()
  if (value) emit('preview', { config: value, document_id: previewDocumentId.value ?? null })
}
function startBuild(rebuild = false): void {
  const value = currentConfig()
  if (value) emit('start', { config: value, rebuild })
}
function load(): void {
  emit('load', { ...form })
}
watch(
  () => props.database.id,
  () => {
    managementOpen.value = false
    previewDocumentId.value = undefined
    canvasMode.value = 'published'
    Object.assign(form, { node_label: '*', max_depth: 2, max_nodes: 100 })
  }
)
watch(
  () => [props.database.id, props.configState?.config],
  () => {
    const saved = props.configState?.config
    Object.assign(configForm, {
      llm_model_id: saved?.llm_model_id ?? props.database.llm_model_id ?? defaults.llm_model_id,
      reasoning_strategy: saved?.reasoning_strategy ?? defaults.reasoning_strategy,
      timeout_seconds: saved?.timeout_seconds ?? defaults.timeout_seconds,
      max_output_tokens: saved?.max_output_tokens ?? defaults.max_output_tokens,
      concurrency: saved?.concurrency ?? defaults.concurrency
    })
    formError.value = ''
  },
  { immediate: true }
)
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
  padding: 20px 0 28px;
}

.graph-canvas-section {
  min-width: 0;
  margin-bottom: 20px;
}
.graph-canvas-section__header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin: 0 0 14px;
}
.graph-canvas-section__heading,
.graph-canvas-section__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.graph-canvas-section__title h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.graph-empty {
  display: grid;
  min-height: 300px;
  align-items: center;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: #fff;
}
.graph-panel {
  min-width: 0;
  margin: 0 0 18px;
  padding: 0 0 18px;
  border-bottom: 1px solid var(--gray-150);
}
.graph-panel h2 {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 500;
}
.graph-panel p {
  margin: 8px 0;
  color: var(--color-text-secondary);
}
.graph-panel :deep(.ant-form-item) {
  margin-bottom: 14px;
}
.graph-preview-select {
  width: 100%;
  margin-top: 8px;
}
.graph-preview {
  border-top: 1px solid var(--gray-150);
  margin-top: 4px;
  padding-top: 14px;
}
.graph-preview h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.graph-number-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.graph-number-fields :deep(.ant-input-number) {
  width: 100%;
}
.graph-advanced :deep(.ant-collapse-header) {
  padding-inline: 0;
}
.graph-advanced :deep(.ant-collapse-content-box) {
  padding-inline: 0;
}
.graph-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}
.graph-muted,
.graph-field-hint {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.graph-preview-result {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 20px;
  margin-top: 16px;
  color: var(--color-text-secondary);
}
.graph-preview-result strong {
  color: var(--color-text);
  font-size: 18px;
  font-weight: 600;
}
.graph-service-alert {
  margin-bottom: 12px;
}
.graph-task-notice {
  margin-bottom: 12px;
}

.graph-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 0 0 12px;
}

.graph-controls > :deep(.ant-select) {
  min-width: 0;
  flex: 1 1 180px;
  max-width: 340px;
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
  margin-top: 4px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.knowledge-graph-page :deep(.knowledge-graph:not(:fullscreen) .knowledge-graph__canvas) {
  height: clamp(340px, calc(100dvh - 420px), 680px);
}

@media (max-width: 640px) {
  .graph-controls {
    align-items: stretch;
  }

  .graph-controls label {
    flex: 1 1 130px;
    justify-content: space-between;
  }

  .graph-controls > :deep(.ant-select) {
    flex-basis: 100%;
    max-width: none;
  }
}
</style>
