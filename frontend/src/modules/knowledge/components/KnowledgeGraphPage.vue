<!-- Adapted from original knowledge graph page, MIT licensed. -->
<template>
  <section class="knowledge-graph-page">
    <WorkspaceToolbar :title="database.name" subtitle="知识图谱">
      <template #actions>
        <a-button @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="刷新知识图谱">
          <a-button
            shape="circle"
            :loading="loading || buildLoading"
            :disabled="starting || configSaving || starting"
            aria-label="刷新知识图谱"
            @click="emit('refresh')"
          >
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-graph-page__content">
      <KnowledgeSectionNav
        active-view="graph"
        :graph-available="available"
        @navigate="emit('navigate', $event)"
      />

      <a-alert v-if="error" class="graph-service-alert" type="error" :message="error" show-icon />
      <a-alert
        v-if="!available && !buildLoading"
        class="graph-service-alert"
        type="warning"
        message="知识图谱服务尚未就绪，请联系管理员完成配置。"
        show-icon
      />
      <a-alert
        class="graph-service-alert"
        type="info"
        message="聚合图谱仅向可读取该版本全部原文的用户开放；原文撤权后会重新校验。"
        show-icon
      />
      <a-alert
        v-if="uncertain"
        class="graph-service-alert"
        type="warning"
        message="提交结果尚未确认；确认会使用原请求标识。"
        show-icon
      />
      <a-button v-if="uncertain || recoverable" @click="emit('recover')"
        >确认原请求或恢复查看</a-button
      >
      <a-spin :spinning="buildLoading">
        <div class="graph-setup-grid">
          <section class="graph-panel" aria-label="图谱配置">
            <div class="graph-panel__heading">
              <h2>图谱配置</h2>
              <span v-if="configState?.config" class="graph-version"
                >v{{ configState.config.version }}</span
              >
            </div>
            <p>选择对话模型，从文档中提取实体与关系。试构建和正式构建均会调用所选模型。</p>
            <p v-if="!canEdit">当前为只读模式。如需调整配置或构建图谱，请联系有编辑权限的成员。</p>
            <a-form layout="vertical" :disabled="controlsDisabled">
              <a-form-item label="图谱对话模型" required>
                <a-select
                  :value="configForm.llm_model_id"
                  aria-label="图谱对话模型"
                  :options="modelOptions"
                  show-search
                  option-filter-prop="label"
                  placeholder="选择已发布的对话模型"
                  :disabled="controlsDisabled"
                  @update:value="configForm.llm_model_id = typeof $event === 'string' ? $event : ''"
                />
              </a-form-item>
              <p v-if="canEdit && available && !buildLoading && !chatOptions.length">
                暂无已发布的对话模型，请联系管理员配置。
              </p>
              <a-collapse ghost class="graph-advanced">
                <a-collapse-panel key="advanced" header="高级选项">
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
                    <a-form-item label="响应超时（秒）">
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
                </a-collapse-panel>
              </a-collapse>
            </a-form>
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

          <section class="graph-panel" aria-label="图谱试构建" aria-live="polite">
            <h2>{{ previewTitle }}</h2>
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
              placeholder="自动选择一个文档"
              :disabled="controlsDisabled"
              @update:value="previewDocumentId = typeof $event === 'string' ? $event : undefined"
            />
            <p v-if="previewStage">
              当前阶段：{{ previewStage
              }}<span v-if="preview?.progress.total_chunks">
                · 已提取 {{ preview?.progress.extracted_chunks ?? '—' }} /
                {{ preview?.progress.total_chunks }} 个切片</span
              >
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
            <p v-if="previewBuilding">试构建正在后台进行，返回此页后会继续更新进度。</p>
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
            </div>
          </section>
        </div>

        <details v-if="jobs?.length" class="graph-panel graph-history" open>
          <summary>图谱任务历史 · {{ jobs.length }}</summary>
          <article v-for="history in jobs" :key="history.id" class="graph-history__item">
            <div class="graph-actions">
              <h3>
                {{
                  history.operation === 'preview'
                    ? '试构建'
                    : history.operation === 'build'
                      ? '正式构建'
                      : history.operation === 'query'
                        ? '图谱检索'
                        : '清理图谱'
                }}
              </h3>
              <a-tag :color="history.status === 'failed' ? 'error' : 'default'">{{
                history.status === 'completed'
                  ? '已完成'
                  : history.status === 'failed'
                    ? '失败'
                    : history.status === 'cancelled'
                      ? '已取消'
                      : history.status === 'interrupted'
                        ? '已中断'
                        : '处理中'
              }}</a-tag
              ><a-button
                v-if="canEdit && history.cancel_allowed"
                size="small"
                :loading="stopping"
                @click="emit('cancel', history)"
                >取消任务</a-button
              >
            </div>
            <p>
              {{ history.processed_documents ?? '—' }}/{{ history.total_documents ?? '—' }} 个文档 ·
              {{ history.progress.extracted_chunks ?? '—' }}/{{
                history.progress.total_chunks ?? '—'
              }}
              个切片 · {{ history.progress.entities ?? '—' }} 个实体 ·
              {{ history.progress.relations ?? '—' }} 个关系
            </p>
            <p v-if="history.error" class="graph-history__error">
              {{ history.error.code }} · {{ history.error.message }}
            </p>
            <details>
              <summary>任务标识与版本</summary>
              <p>{{ history.id }} · {{ history.phase }}</p>
              <p>
                图谱版本：{{ history.graph_revision ?? '尚未发布' }} ·
                {{ formatTime(history.created_at) }}
              </p>
            </details>
          </article>
        </details>
        <section class="graph-panel graph-build-panel" aria-label="图谱构建" aria-live="polite">
          <h2>{{ buildTitle }}</h2>
          <p v-if="job?.total_documents">
            已处理 {{ job?.processed_documents ?? '—' }} / {{ job?.total_documents }} 个文档
          </p>
          <p v-else>上传文档后可构建图谱，补建会处理尚未完成的文档。</p>
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
          <p v-if="buildStage">
            当前阶段：{{ buildStage
            }}<span v-if="job?.progress.total_chunks">
              · 已提取 {{ job?.progress.extracted_chunks ?? '—' }} /
              {{ job?.progress.total_chunks }} 个切片</span
            >
          </p>
          <p v-if="building">任务将在后台继续，可切换页面查看文档或进行检索。</p>
          <p v-if="configState?.requires_rebuild">当前配置需要构建新版本，完成后自动切换。</p>
          <p v-if="readable && (building || ['failed', 'interrupted'].includes(job?.status ?? ''))">
            下方仍可查看已完成的图谱版本。
          </p>
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
            <span v-if="database.enable_knowledge_graph" class="graph-muted"
              >重新构建会创建新版本，完成后自动切换。</span
            >
            <a-button v-if="job?.cancel_allowed" :loading="stopping" @click="emit('cancel', job)"
              >取消图谱任务</a-button
            >
            <a-button
              v-if="preview?.cancel_allowed"
              :loading="stopping"
              @click="emit('cancel', preview)"
              >取消试构建</a-button
            >
          </div>
        </section>
      </a-spin>

      <section v-if="readable && available" class="graph-panel graph-browser" aria-label="图谱浏览">
        <h2>图谱浏览</h2>
        <p>当前图谱版本：{{ configState?.active_revision }}</p>
        <div class="graph-controls">
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
          <a-button type="primary" :loading="loading" @click="load">加载图谱</a-button>
        </div>

        <a-alert
          v-if="graph.truncated"
          class="graph-alert"
          type="info"
          message="当前展示部分节点。可选择具体实体或调整深度继续探索。"
          show-icon
        />
        <div v-if="graph.nodes.length" class="graph-summary">
          当前子图：{{ graph.nodes.length }} 个节点 · {{ graph.links.length }} 个关系 ·
          {{ graph.categories.length }} 种实体类型
        </div>
        <KnowledgeGraph v-if="graph.nodes.length" :graph="graph" />
        <WorkspaceState
          v-else
          :kind="loading ? 'loading' : 'empty'"
          :title="loading ? '正在加载图谱' : '暂无图谱节点'"
        />
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { computed, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Collapse as ACollapse,
  CollapsePanel as ACollapsePanel,
  Form as AForm,
  FormItem as AFormItem,
  InputNumber as AInputNumber,
  Progress as AProgress,
  Select as ASelect,
  Spin as ASpin,
  Tooltip as ATooltip,
  Tag as ATag
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { formatTime } from '../presentation.ts'
import KnowledgeGraph from './KnowledgeGraph.vue'
import KnowledgeSectionNav from './KnowledgeSectionNav.vue'
import { graphIsActive, graphProgress, graphStatusLabel } from '../graphPresentation.ts'
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
  back: []
  navigate: [view: string]
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
const buildStage = computed(() => props.job?.phase ?? ''),
  previewStage = computed(() => props.preview?.phase ?? '')
const previewResult = computed<GraphPreviewResult | null>(() =>
  props.preview?.result && 'seconds' in props.preview.result ? props.preview.result : null
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
    disabled: !props.configState?.supported_reasoning_strategies.some(
      (value) => value === option.value
    )
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
    previewDocumentId.value = undefined
    Object.assign(form, { node_label: '*', max_depth: 2, max_nodes: 100 })
  }
)
watch(
  () => [props.database.id, props.configState?.config],
  () => {
    Object.assign(
      configForm,
      defaults,
      props.configState?.config ?? { llm_model_id: props.database.llm_model_id ?? '' }
    )
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
  padding: 20px 24px 28px;
}

.graph-history {
  margin-bottom: 18px;
}
.graph-history > summary {
  cursor: pointer;
  font-size: 16px;
  font-weight: 500;
}
.graph-history__item {
  padding: 12px 0;
  border-bottom: 1px solid var(--gray-150);
}
.graph-history__item:last-child {
  border: 0;
  padding-bottom: 0;
}
.graph-history__item h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
}
.graph-history__item details {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.graph-history__item details summary {
  cursor: pointer;
}
.graph-history__item p {
  overflow-wrap: anywhere;
}
.graph-history__item .graph-history__error {
  color: var(--color-error);
}
.graph-panel__heading {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.graph-version {
  margin-left: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 400;
}
.graph-setup-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 18px;
  margin-bottom: 18px;
}
.graph-panel {
  min-width: 0;
  padding: 18px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
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
.graph-build-panel {
  margin-bottom: 18px;
}
.graph-preview-select {
  width: 100%;
  margin-top: 8px;
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
  margin-bottom: 18px;
}

@media (max-width: 1100px) {
  .graph-setup-grid {
    grid-template-columns: minmax(0, 1fr);
  }
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
