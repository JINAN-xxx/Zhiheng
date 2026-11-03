<!-- Adapted from original model center, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  InputPassword as AInputPassword,
  Drawer as ADrawer,
  Tabs as ATabs,
  TabPane as ATabPane,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  Select as ASelect,
  Tag as ATag,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal,
  Switch as ASwitch
} from 'ant-design-vue'
import {
  PlusOutlined,
  ReloadOutlined,
  EllipsisOutlined,
  CopyOutlined,
  SearchOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
  ApiOutlined
} from '@ant-design/icons-vue'
import { useWindowSize } from '@vueuse/core'
import type { TableColumnsType } from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import ResourceCard from '../../../shared/components/ResourceCard.vue'
import ResourceCardGrid from '../../../shared/components/ResourceCardGrid.vue'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ModelRatesDialog from '../components/ModelRatesDialog.vue'
import ModelActions from '../components/ModelActions.vue'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal, runStatusLabel } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import {
  cancelModelTest,
  createConnection,
  createModel,
  getConnections,
  getModels,
  observeModelTest,
  startModelTest,
  updateConnection,
  updateModel,
  controlConnection,
  controlModel
} from '../api/index.ts'
import type { ChatModel, ModelConnection } from '../api/index.ts'
const canReadModels = computed(() =>
  hasAnyPermission(identity.value, 'ai:models:read', 'ai:models:write')
)
const canWriteModels = computed(() => hasAnyPermission(identity.value, 'ai:models:write'))
const canTestModels = computed(() => hasAnyPermission(identity.value, 'ai:models:test'))
const canReadRates = computed(() =>
  hasAnyPermission(identity.value, 'ai:rates:read', 'ai:rates:write')
)
const canWriteRates = computed(() => hasAnyPermission(identity.value, 'ai:rates:write'))
const hasModelActions = computed(
  () => canWriteModels.value || canTestModels.value || canReadRates.value
)
function setActiveTab(value: unknown): void {
  activeTab.value = value === 'credentials' && canReadModels.value ? 'credentials' : 'platform'
}
const activeTab = ref('platform')
const modelSearch = ref('')
const modelType = ref('all')
const modelView = ref<'cards' | 'list'>('cards')
const modelFilters = [
  { value: 'all', label: '全部模型' },
  { value: 'chat', label: '对话模型' },
  { value: 'embedding', label: '向量模型' },
  { value: 'rerank', label: '重排序模型' }
]
const { width } = useWindowSize()
const isMobile = computed(() => width.value <= 760)
const lastSuccessAt = ref('')
const modelColumns = [
  { title: '模型', key: 'model', width: 240 },
  { title: '类型', key: 'type', width: 100 },
  { title: '连接状态', key: 'binding', width: 120 },
  { title: '状态', key: 'status', width: 100 },
  { title: '输出上限', dataIndex: 'max_output_tokens', width: 110 },
  { title: '操作', key: 'actions', width: 240 }
]
const connectionColumns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 180 },
  { title: '服务商', key: 'provider', width: 150 },
  { title: 'API 地址', dataIndex: 'base_url', key: 'base_url', ellipsis: true },
  { title: '凭据状态', key: 'status', width: 130 },
  { title: '连接状态', key: 'enabled', width: 110 },
  { title: '关联模型', key: 'models', width: 100 },
  { title: '操作', key: 'actions', width: 160 }
]
const visibleModelColumns = computed<TableColumnsType<ChatModel>>(() =>
  isMobile.value
    ? [
        { title: '模型代码', key: 'model' },
        ...(hasModelActions.value
          ? [{ title: '操作', key: 'actions', width: 64, align: 'right' as const }]
          : [])
      ]
    : modelColumns.filter(
        (column) =>
          (column.key !== 'binding' || canReadModels.value) &&
          (column.key !== 'actions' || hasModelActions.value)
      )
)
const visibleConnectionColumns = computed(() =>
  isMobile.value
    ? connectionColumns.filter(
        (column) => column.key === 'name' || (column.key === 'actions' && canWriteModels.value)
      )
    : connectionColumns.filter((column) => column.key !== 'actions' || canWriteModels.value)
)
const connections = ref<ModelConnection[]>([])
const models = ref<ChatModel[]>([])
const filteredModels = computed(() => {
  const search = modelSearch.value.trim().toLowerCase()
  return models.value.filter(
    (model) =>
      (modelType.value === 'all' || model.model_type === modelType.value) &&
      [model.name, model.id, model.upstream_model, connectionName(model.connection_id)]
        .join(' ')
        .toLowerCase()
        .includes(search)
  )
})
function connectionName(id: string): string {
  return connections.value.find((connection) => connection.id === id)?.name ?? '连接不可用'
}
function connectionEnabled(id: string): boolean {
  return connections.value.some((connection) => connection.id === id && connection.enabled)
}
function connectionDisabled(id: string): boolean {
  return connections.value.find((connection) => connection.id === id)?.enabled === false
}
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const errorDetails = ref<unknown>()
const modalError = ref('')
const connectionOpen = ref(false)
const modelOpen = ref(false)
const editingConnection = ref<ModelConnection | null>(null)
const editingModel = ref<ChatModel | null>(null)
const detailModel = ref<ChatModel | null>(null)
const rateModel = ref<ChatModel | null>(null)
const connectionForm = reactive({ name: '', base_url: '', api_key: '' })
const modelForm = reactive<{
  name: string
  connection_id: string
  upstream_model: string
  max_output_tokens: number | null
  context_window_tokens: number | null
  model_type: ChatModel['model_type']
  vision_enabled: boolean
}>({
  name: '',
  connection_id: '',
  upstream_model: '',
  max_output_tokens: 512,
  context_window_tokens: null,
  model_type: 'chat',
  vision_enabled: false
})
const testRun = ref<RunSnapshot | null>(null)
const testingModel = ref('')
const testDisconnected = ref(false)
const scope = new RequestScope()
const mutationScope = new RequestScope()
const testScope = new RequestScope()
let copyEpoch = 0
watch(
  detailModel,
  () => {
    copyEpoch += 1
  },
  { flush: 'sync' }
)
let testIntent: { modelId: string; requestId: string } | null = null
const testBusy = computed(
  () => testingModel.value !== '' && (testRun.value === null || !isTerminal(testRun.value))
)
const connectionOptions = computed(() =>
  connections.value
    .filter((item) => item.enabled)
    .map((item) => ({ value: item.id, label: item.name }))
)
onBeforeUnmount(() => {
  scope.dispose()
  mutationScope.dispose()
  testScope.dispose()
  connectionForm.api_key = ''
})
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  errorDetails.value = undefined
  try {
    const [items, availableModels] = await Promise.all([
      canReadModels.value ? getConnections(context.signal) : Promise.resolve([]),
      getModels(context.signal, canReadModels.value)
    ])
    if (context.isCurrent()) {
      connections.value = items
      models.value = availableModels
      lastSuccessAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
onMounted(() => {
  void load()
})
async function copyModelCode(value: string): Promise<void> {
  if (!value.trim()) return
  const context = scope.current()
  const epoch = ++copyEpoch
  try {
    await navigator.clipboard.writeText(value)
  } catch {
    if (!context.isCurrent() || epoch !== copyEpoch) return
    error.value = '复制失败，请手动复制模型代码'
    errorDetails.value = undefined
  }
}
function openConnection(connection: ModelConnection | null = null): void {
  if (!canWriteModels.value) return
  mutationScope.replace()
  saving.value = false
  editingConnection.value = connection
  Object.assign(connectionForm, {
    name: connection?.name ?? '',
    base_url: connection?.base_url ?? '',
    api_key: ''
  })
  connectionOpen.value = true
  modalError.value = ''
}
function openModel(model: ChatModel | null = null): void {
  if (!canWriteModels.value) return
  mutationScope.replace()
  saving.value = false
  editingModel.value = model
  Object.assign(modelForm, {
    name: model?.name ?? '',
    connection_id:
      model?.connection_id ??
      (connections.value.filter((item) => item.enabled).length === 1
        ? (connections.value.find((item) => item.enabled)?.id ?? '')
        : ''),
    upstream_model: model?.upstream_model ?? '',
    max_output_tokens: model?.max_output_tokens ?? 512,
    context_window_tokens: model?.context_window_tokens ?? null,
    model_type: model?.model_type ?? 'chat',
    vision_enabled: model?.vision_enabled ?? false
  })
  modelOpen.value = true
  modalError.value = ''
}
function closeConnection(): void {
  connectionOpen.value = false
  connectionForm.api_key = ''
  mutationScope.replace()
  saving.value = false
  modalError.value = ''
}
async function saveConnection(): Promise<void> {
  if (saving.value || !canWriteModels.value) return
  const editing = editingConnection.value
  if (
    !connectionForm.name.trim() ||
    !connectionForm.base_url.trim() ||
    (!editing && !connectionForm.api_key)
  ) {
    modalError.value = '请填写连接名称、API 地址和密钥'
    return
  }
  const context = mutationScope.replace()
  saving.value = true
  modalError.value = ''
  try {
    const body = { name: connectionForm.name.trim(), base_url: connectionForm.base_url.trim() }
    const connection = editing
      ? await updateConnection(
          editing.id,
          { ...body, ...(connectionForm.api_key ? { api_key: connectionForm.api_key } : {}) },
          context.signal
        )
      : await createConnection({ ...body, api_key: connectionForm.api_key }, context.signal)
    if (context.isCurrent()) {
      scope.replace()
      loading.value = false
      if (editing)
        connections.value = connections.value.map((item) =>
          item.id === connection.id ? connection : item
        )
      else connections.value.push(connection)
      connectionOpen.value = false
      if (!editing) activeTab.value = 'platform'
      connectionForm.name = ''
      connectionForm.base_url = ''
      modelForm.connection_id = connection.id
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) modalError.value = errorMessage(reason)
  } finally {
    connectionForm.api_key = ''
    if (context.isCurrent()) saving.value = false
  }
}
function closeModel(): void {
  modelOpen.value = false
  mutationScope.replace()
  saving.value = false
  modalError.value = ''
}
function setConnectionId(value: unknown): void {
  modelForm.connection_id = typeof value === 'string' ? value : ''
}
function setMaxOutputTokens(value: unknown): void {
  modelForm.max_output_tokens = typeof value === 'number' ? value : null
}
function setModelType(value: unknown): void {
  if (value === 'chat' || value === 'embedding' || value === 'rerank') modelForm.model_type = value
}
const modelTypeLabel = (value: ChatModel['model_type']) =>
  ({ chat: '对话', embedding: '向量', rerank: '重排序' })[value]
async function changeModel(
  model: ChatModel,
  action: 'enable' | 'disable' | 'publish' | 'unpublish' | 'delete'
): Promise<void> {
  if (saving.value || !canWriteModels.value) return
  const context = mutationScope.replace()
  const execute = async () => {
    if (!context.isCurrent() || !canWriteModels.value) return
    saving.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      await controlModel(model.id, action, context.signal)
      if (context.isCurrent()) await load()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        errorDetails.value = reason instanceof ApiError ? reason.details : undefined
      }
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  if (action === 'delete' || action === 'disable' || action === 'unpublish')
    Modal.confirm({
      title:
        action === 'delete'
          ? '删除该模型？模型将自动停用、取消发布并从目录移除，历史调用与账单记录仍会保留。当前依赖或在途使用需要先处理。'
          : '调整后，新请求将不能使用该模型。是否继续？',
      okText: '确定',
      cancelText: '取消',
      onOk: execute
    })
  else await execute()
}
async function changeConnection(
  connection: ModelConnection,
  action: 'enable' | 'disable' | 'delete'
): Promise<void> {
  if (saving.value || !canWriteModels.value) return
  const context = mutationScope.replace()
  const execute = async () => {
    if (!context.isCurrent() || !canWriteModels.value) return
    saving.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      await controlConnection(connection.id, action, context.signal)
      if (context.isCurrent()) await load()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        errorDetails.value = reason instanceof ApiError ? reason.details : undefined
      }
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  if (action === 'enable') await execute()
  else
    Modal.confirm({
      title:
        action === 'delete'
          ? '删除该凭据？当前模型或在途请求仍在使用时需要先处理，历史调用记录仍会保留。'
          : '停用后，新请求将不能使用此连接。是否继续？',
      okText: '确定',
      cancelText: '取消',
      onOk: execute
    })
}
async function saveModel(): Promise<void> {
  if (saving.value || !canWriteModels.value) return
  const maxOutputTokens = modelForm.max_output_tokens
  if (
    !modelForm.name.trim() ||
    !modelForm.connection_id ||
    !modelForm.upstream_model.trim() ||
    maxOutputTokens === null ||
    !Number.isInteger(maxOutputTokens) ||
    maxOutputTokens < 1 ||
    maxOutputTokens > 131072
  ) {
    modalError.value = '请填写模型信息，输出上限须为 1–131072 的整数'
    return
  }
  const windowTokens = modelForm.context_window_tokens
  if (
    windowTokens !== null &&
    (!Number.isInteger(windowTokens) || windowTokens < 256 || windowTokens > 2097152)
  ) {
    modalError.value = '上下文窗口须为 256–2097152 的整数，或留空使用平台输入预算'
    return
  }
  const context = mutationScope.replace()
  saving.value = true
  modalError.value = ''
  try {
    const body = {
      ...modelForm,
      vision_enabled: modelForm.model_type === 'chat' && modelForm.vision_enabled,
      max_output_tokens: maxOutputTokens,
      name: modelForm.name.trim(),
      upstream_model: modelForm.upstream_model.trim()
    }
    const model = editingModel.value
      ? await updateModel(
          editingModel.value.id,
          {
            name: body.name,
            connection_id: body.connection_id,
            upstream_model: body.upstream_model,
            max_output_tokens: body.max_output_tokens,
            context_window_tokens: body.context_window_tokens,
            vision_enabled: body.model_type === 'chat' && body.vision_enabled
          },
          context.signal
        )
      : await createModel(body, context.signal)
    if (context.isCurrent()) {
      scope.replace()
      loading.value = false
      if (editingModel.value)
        models.value = models.value.map((item) => (item.id === model.id ? model : item))
      else models.value.push(model)
      modelOpen.value = false
      modelForm.name = ''
      modelForm.upstream_model = ''
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) modalError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
async function followTest(
  initial: RunSnapshot,
  context: ReturnType<RequestScope['current']>
): Promise<void> {
  await observeModelTest(
    initial,
    (run) => {
      if (context.isCurrent() && acceptSnapshot(testRun.value, run)) testRun.value = run
    },
    context.signal
  )
}
async function testModel(model: ChatModel): Promise<void> {
  if (
    testBusy.value ||
    !canTestModels.value ||
    !model.enabled ||
    connectionDisabled(model.connection_id)
  )
    return
  const context = testScope.replace()
  testingModel.value = model.id
  testRun.value = null
  testDisconnected.value = false
  error.value = ''
  errorDetails.value = undefined
  try {
    if (!testIntent || testIntent.modelId !== model.id)
      testIntent = { modelId: model.id, requestId: crypto.randomUUID() }
    const run = await startModelTest(model.id, testIntent.requestId, context.signal)
    if (!context.isCurrent()) return
    testIntent = null
    testRun.value = run
    await followTest(run, context)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      testDisconnected.value = testRun.value !== null
      if (!testRun.value) testingModel.value = ''
    }
  }
}
async function resumeTest(): Promise<void> {
  if (!testRun.value || !canTestModels.value) return
  const context = testScope.replace()
  error.value = ''
  errorDetails.value = undefined
  testDisconnected.value = false
  try {
    await followTest(testRun.value, context)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      testDisconnected.value = true
    }
  }
}
async function stopTest(): Promise<void> {
  if (!testRun.value || !canTestModels.value) return
  const context = testScope.current()
  try {
    const run = await cancelModelTest(testRun.value.id, context.signal)
    if (context.isCurrent() && acceptSnapshot(testRun.value, run)) testRun.value = run
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  }
}
</script>
<template>
  <main class="app-container ai-model-center">
    <WorkspaceToolbar title="模型目录"
      ><template #actions
        ><AButton shape="circle" aria-label="刷新当前视图" :loading="loading" @click="load"
          ><ReloadOutlined /></AButton
        ><AButton
          v-if="canWriteModels && (activeTab === 'platform' || isMobile)"
          type="primary"
          aria-label="接入模型"
          :disabled="!connections.length"
          @click="openModel()"
          ><PlusOutlined />接入模型</AButton
        ><AButton
          v-if="canWriteModels && activeTab !== 'platform' && !isMobile"
          type="primary"
          aria-label="新增凭据"
          @click="openConnection()"
          ><PlusOutlined />新增凭据</AButton
        ></template
      ></WorkspaceToolbar
    >
    <section class="model-workspace">
      <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" />
      <ResourceReferences :details="errorDetails" />
      <ATabs :active-key="activeTab" class="workspace-tabs" @update:active-key="setActiveTab">
        <ATabPane key="platform" tab="平台模型"
          ><section class="workspace-panel">
            <div class="model-catalog-toolbar">
              <div class="model-filters" aria-label="模型类型筛选">
                <button
                  v-for="filter in modelFilters"
                  :key="filter.value"
                  type="button"
                  :class="{ active: modelType === filter.value }"
                  :aria-pressed="modelType === filter.value"
                  @click="modelType = filter.value"
                >
                  {{ filter.label }}
                </button>
              </div>
              <div class="catalog-tools">
                <span v-if="!isMobile" class="catalog-total"
                  >{{ filteredModels.length }} 个模型</span
                >
                <AInput
                  v-model:value="modelSearch"
                  allow-clear
                  aria-label="搜索模型"
                  :placeholder="canReadModels ? '搜索模型名称、代码或连接' : '搜索模型名称或代码'"
                >
                  <template #prefix><SearchOutlined /></template>
                </AInput>
                <div class="view-switch" aria-label="模型展示方式">
                  <AButton
                    :type="modelView === 'cards' ? 'primary' : 'default'"
                    aria-label="卡片视图"
                    :aria-pressed="modelView === 'cards'"
                    @click="modelView = 'cards'"
                    ><AppstoreOutlined
                  /></AButton>
                  <AButton
                    :type="modelView === 'list' ? 'primary' : 'default'"
                    aria-label="列表视图"
                    :aria-pressed="modelView === 'list'"
                    @click="modelView = 'list'"
                    ><UnorderedListOutlined
                  /></AButton>
                </div>
              </div>
            </div>
            <div v-if="isMobile" class="panel-toolbar">
              <div class="panel-summary">
                <span>{{ models.length }} 个平台模型</span
                ><span v-if="lastSuccessAt">更新于 {{ lastSuccessAt }}</span>
              </div>
              <AButton shape="circle" :loading="loading" aria-label="刷新平台模型" @click="load"
                ><ReloadOutlined
              /></AButton>
            </div>
            <ResourceCardGrid
              v-if="!isMobile && modelView === 'cards' && filteredModels.length"
              class="model-grid"
              aria-label="平台模型"
            >
              <ResourceCard
                v-for="model in filteredModels"
                :key="model.id"
                class="model-card"
                :title="model.name"
                :subtitle="`${modelTypeLabel(model.model_type)}模型`"
                :status="model.enabled ? (model.published ? '已启用' : '草稿') : '已停用'"
                :tone="model.enabled && model.published ? 'positive' : 'neutral'"
                :icon-tone="
                  model.model_type === 'embedding'
                    ? 'green'
                    : model.model_type === 'rerank'
                      ? 'amber'
                      : 'primary'
                "
                interactive
                :open-label="`查看模型详情：${model.name}`"
                @open="detailModel = model"
              >
                <template #icon><ApiOutlined /></template>
                <template #title-action>
                  <button
                    type="button"
                    class="copy-button resource-id-copy"
                    aria-label="复制模型代码"
                    title="复制模型代码"
                    :disabled="!model.id.trim()"
                    @click="copyModelCode(model.id)"
                  >
                    <CopyOutlined />
                  </button>
                </template>
                <dl class="model-card__details">
                  <div>
                    <dt>上游模型</dt>
                    <dd class="model-card__upstream" :title="model.upstream_model">
                      {{ model.upstream_model }}
                    </dd>
                  </div>
                  <div v-if="model.model_type === 'chat'" class="model-card__output">
                    <dt>最大输出</dt>
                    <dd>{{ model.max_output_tokens.toLocaleString() }} Token</dd>
                  </div>
                </dl>
                <p v-if="model.model_type !== 'chat'" class="model-card__purpose">
                  {{
                    model.model_type === 'embedding'
                      ? '用于文本向量化与知识检索'
                      : '用于检索结果相关性排序'
                  }}
                </p>
                <template #footer-info>
                  <span
                    v-if="canReadModels"
                    class="model-card__connection"
                    :title="connectionName(model.connection_id)"
                    >{{ connectionName(model.connection_id) }}</span
                  >
                  <span
                    v-if="canReadModels && connectionDisabled(model.connection_id)"
                    class="model-card__connection-warning"
                    >连接已停用，暂不可测试</span
                  >
                </template>
                <template #actions>
                  <ModelActions
                    presentation="card"
                    :model="model"
                    :busy="testBusy"
                    :testing="testingModel === model.id"
                    :can-write="canWriteModels"
                    :can-read-rates="canReadRates"
                    :can-test="canTestModels"
                    :test-disabled="connectionDisabled(model.connection_id)"
                    @edit="openModel(model)"
                    @rates="canReadRates && (rateModel = model)"
                    @test="testModel(model)"
                    @control="changeModel(model, $event)"
                  />
                </template>
              </ResourceCard>
            </ResourceCardGrid>
            <ResourceTable
              v-else-if="filteredModels.length || loading"
              class="model-table"
              :columns="visibleModelColumns"
              :scroll-x="isMobile ? '100%' : 850"
              :rows="filteredModels"
              :loading="loading"
              ><template #bodyCell="{ column, record: model }">
                <template v-if="column.key === 'model'"
                  ><div class="mobile-model-cell">
                    <span class="model-display-name">{{ model.name }}</span>
                    <div class="resource-value resource-value--secondary">
                      <span class="resource-text" :title="model.id">{{ model.id }}</span>
                      <button
                        type="button"
                        class="copy-button"
                        aria-label="复制模型代码"
                        title="复制模型代码"
                        :disabled="!model.id.trim()"
                        @click="copyModelCode(model.id)"
                      >
                        <CopyOutlined />
                      </button>
                    </div>
                    <div v-if="isMobile" class="mobile-model-meta">
                      <ATag color="blue">{{ modelTypeLabel(model.model_type) }}</ATag
                      ><ATag
                        v-if="canReadModels"
                        :color="connectionEnabled(model.connection_id) ? 'success' : 'default'"
                        >{{
                          connectionEnabled(model.connection_id) ? '连接已启用' : '连接不可用'
                        }}</ATag
                      ><ATag :color="model.enabled ? 'success' : 'default'">{{
                        model.enabled ? (model.published ? '已启用' : '草稿') : '已停用'
                      }}</ATag>
                    </div>
                  </div></template
                >
                <template v-else-if="column.key === 'type'"
                  ><ATag>{{ modelTypeLabel(model.model_type) }}</ATag></template
                >
                <template v-else-if="column.key === 'binding'"
                  ><ATag :color="connectionEnabled(model.connection_id) ? 'success' : 'default'">{{
                    connectionEnabled(model.connection_id) ? '已启用' : '不可用'
                  }}</ATag></template
                >
                <template v-else-if="column.key === 'status'"
                  ><ATag :color="model.enabled ? 'success' : 'default'">{{
                    model.enabled ? (model.published ? '已启用' : '草稿') : '已停用'
                  }}</ATag></template
                >
                <template v-else-if="column.key === 'actions'">
                  <ModelActions
                    :model="model"
                    :busy="testBusy"
                    :testing="testingModel === model.id"
                    :can-write="canWriteModels"
                    :can-read-rates="canReadRates"
                    :can-test="canTestModels"
                    :test-disabled="connectionDisabled(model.connection_id)"
                    @edit="openModel(model)"
                    @rates="canReadRates && (rateModel = model)"
                    @test="testModel(model)"
                    @control="changeModel(model, $event)"
                  />
                </template> </template
            ></ResourceTable>
            <WorkspaceState
              v-else
              :title="models.length ? '没有匹配的模型' : '暂无平台模型'"
              :message="
                models.length
                  ? '试试其他模型类型或关键词。'
                  : !canWriteModels
                    ? '当前暂无可用模型，请联系平台管理员。'
                    : connections.length
                      ? '点击接入模型，选择已保存的连接。'
                      : '请先在凭据管理中添加模型连接。'
              "
            /></section
        ></ATabPane>
        <ATabPane v-if="canReadModels" key="credentials" tab="凭据管理"
          ><section class="workspace-panel">
            <div v-if="isMobile" class="panel-toolbar">
              <div class="panel-summary">
                <span v-if="lastSuccessAt">更新于 {{ lastSuccessAt }}</span>
              </div>
              <AButton
                v-if="canWriteModels"
                type="primary"
                aria-label="新增凭据"
                @click="openConnection()"
                ><PlusOutlined />新建凭据</AButton
              >
            </div>
            <ResourceTable
              v-if="connections.length || loading"
              class="model-table"
              :columns="visibleConnectionColumns"
              :scroll-x="isMobile ? '100%' : 850"
              :rows="connections"
              :loading="loading"
              ><template #bodyCell="{ column, record: connection }"
                ><template v-if="column.key === 'name'"
                  ><div class="mobile-model-cell">
                    <span class="model-display-name" :title="connection.name">{{
                      connection.name
                    }}</span>
                    <ATag v-if="isMobile">{{ connection.enabled ? '已启用' : '已停用' }}</ATag>
                  </div></template
                ><template v-else-if="column.key === 'provider'">OpenAI 兼容</template
                ><template v-else-if="column.key === 'status'"
                  ><ATag :color="connection.has_api_key ? 'success' : 'warning'">{{
                    connection.has_api_key ? '密钥已配置' : '未配置密钥'
                  }}</ATag></template
                ><template v-else-if="column.key === 'enabled'"
                  ><ATag :color="connection.enabled ? 'success' : 'default'">{{
                    connection.enabled ? '已启用' : '已停用'
                  }}</ATag></template
                >
                <template v-else-if="column.key === 'models'"
                  >{{
                    models.filter((model) => model.connection_id === connection.id).length
                  }}
                  个</template
                >
                <template v-else-if="column.key === 'actions' && canWriteModels"
                  ><AButton
                    type="link"
                    size="small"
                    aria-label="编辑凭据"
                    @click="openConnection(connection)"
                    >编辑</AButton
                  ><ADropdown :trigger="['click']"
                    ><AButton type="text" aria-label="更多凭据操作"><EllipsisOutlined /></AButton
                    ><template #overlay
                      ><AMenu
                        ><AMenuItem
                          @click="
                            changeConnection(connection, connection.enabled ? 'disable' : 'enable')
                          "
                          >{{ connection.enabled ? '停用' : '启用' }}</AMenuItem
                        ><AMenuItem danger @click="changeConnection(connection, 'delete')"
                          >删除凭据</AMenuItem
                        ></AMenu
                      ></template
                    ></ADropdown
                  ></template
                ></template
              ></ResourceTable
            >
            <WorkspaceState
              v-else
              title="暂无服务商凭据"
              :message="
                canWriteModels ? '添加 OpenAI 兼容的模型服务连接。' : '当前暂无服务商凭据。'
              "
            /></section></ATabPane
      ></ATabs>
      <section v-if="testRun && canTestModels" class="test-result">
        <div class="test-result__heading">
          <h2>
            连接测试 <ATag>{{ runStatusLabel[testRun.status] }}</ATag>
          </h2>
          <div>
            <AButton v-if="testDisconnected" @click="resumeTest">恢复查看</AButton
            ><AButton v-if="!isTerminal(testRun)" @click="stopTest">停止测试</AButton>
          </div>
        </div>
        <pre>{{
          testRun.text ||
          (testRun.result
            ? JSON.stringify(testRun.result, null, 2)
            : isTerminal(testRun)
              ? '暂无返回内容'
              : '等待模型响应…')
        }}</pre>
        <p v-if="testRun.error" class="run-error">{{ testRun.error.message }}</p>
        <p class="muted small">
          连接测试会计入用量。{{
            testRun.usage.status === 'known'
              ? `输入 ${testRun.usage.input_tokens ?? '待核对'} · 输出 ${testRun.usage.output_tokens ?? '待核对'} tokens`
              : '用量待核对'
          }}
        </p>
      </section>
    </section>
  </main>
  <ADrawer
    :open="connectionOpen && canWriteModels"
    :title="editingConnection ? '编辑服务商凭据' : '新建服务商凭据'"
    width="min(620px, 100vw)"
    root-class-name="model-form-drawer"
    destroy-on-close
    @close="closeConnection"
  >
    <AAlert v-if="modalError" :message="modalError" type="error" show-icon class="space-bottom" />
    <AForm layout="vertical"
      ><AFormItem label="连接名称" required
        ><AInput
          v-model:value="connectionForm.name"
          placeholder="例如：团队模型服务"
          :maxlength="80" /></AFormItem
      ><AFormItem label="API 地址" required
        ><AInput
          v-model:value="connectionForm.base_url"
          placeholder="https://api.example.com/v1"
          :maxlength="2048" /></AFormItem
      ><AFormItem label="API Key" :required="!editingConnection"
        ><AInputPassword
          v-model:value="connectionForm.api_key"
          autocomplete="off"
          :placeholder="
            editingConnection ? '留空保留当前密钥' : '密钥仅用于本次提交，保存后不会回显'
          " /></AFormItem
    ></AForm>
    <template #footer
      ><div class="footer-actions">
        <AButton @click="closeConnection">取消</AButton
        ><AButton type="primary" :loading="saving" @click="saveConnection">保存连接</AButton>
      </div></template
    >
  </ADrawer>
  <ADrawer
    :open="modelOpen && canWriteModels"
    :title="editingModel ? '编辑模型' : '接入模型'"
    width="min(840px, 100vw)"
    root-class-name="model-onboarding-drawer"
    destroy-on-close
    @close="closeModel"
  >
    <AAlert v-if="modalError" :message="modalError" type="error" show-icon class="space-bottom" />
    <div class="onboarding-form">
      <section class="form-section">
        <div class="section-heading">
          <h3>模型信息</h3>
          <p>先确定模型用途，再填写服务商提供的模型名称。</p>
        </div>
        <AForm layout="vertical">
          <AFormItem label="模型类型"
            ><ARadioGroup
              :value="modelForm.model_type"
              :disabled="editingModel !== null"
              class="model-type-options"
              button-style="solid"
              @update:value="setModelType"
              ><ARadioButton value="chat">对话</ARadioButton
              ><ARadioButton value="embedding">向量</ARadioButton
              ><ARadioButton value="rerank">重排序</ARadioButton></ARadioGroup
            ></AFormItem
          >
          <AFormItem v-if="modelForm.model_type === 'chat'" label="视觉能力"
            ><ASwitch
              :checked="modelForm.vision_enabled"
              aria-label="启用视觉能力"
              @update:checked="(value) => (modelForm.vision_enabled = value === true)"
            />
            <p class="muted small">仅在上游模型支持图片输入时启用。</p></AFormItem
          >
          <div class="form-grid">
            <AFormItem label="上游模型标识" required
              ><AInput
                v-model:value="modelForm.upstream_model"
                placeholder="填写服务商提供的模型 ID"
                :maxlength="200" /></AFormItem
            ><AFormItem label="显示名称" required
              ><AInput v-model:value="modelForm.name" placeholder="例如：日常助手" :maxlength="80"
            /></AFormItem>
          </div>
        </AForm>
      </section>
      <section class="form-section">
        <div class="section-heading">
          <h3>连接配置</h3>
          <p>复用已保存的连接。</p>
        </div>
        <AForm layout="vertical"
          ><AFormItem label="模型连接" required
            ><ASelect
              :value="modelForm.connection_id || undefined"
              :options="connectionOptions"
              placeholder="选择模型连接"
              @update:value="setConnectionId" /></AFormItem
          ><AFormItem label="最大输出 tokens" required
            ><AInputNumber
              :value="modelForm.max_output_tokens ?? ''"
              :min="1"
              :max="131072"
              :precision="0"
              class="full-width"
              @update:value="setMaxOutputTokens" /></AFormItem
          ><AFormItem
            label="上下文窗口 tokens（可选）"
            extra="填写模型实际窗口；留空时使用平台输入预算。"
            ><AInputNumber
              :value="modelForm.context_window_tokens ?? ''"
              :min="256"
              :max="2097152"
              :precision="0"
              class="full-width"
              placeholder="使用平台输入预算"
              @update:value="
                modelForm.context_window_tokens = typeof $event === 'number' ? $event : null
              " /></AFormItem
        ></AForm>
      </section>
    </div>
    <template #footer
      ><div class="onboarding-footer">
        <span class="footer-status">{{
          canTestModels ? '保存后可在模型目录中测试连接。' : '保存后可在模型目录查看模型。'
        }}</span>
        <div class="footer-actions">
          <AButton @click="closeModel">取消</AButton
          ><AButton type="primary" :loading="saving" @click="saveModel">保存模型</AButton>
        </div>
      </div></template
    >
  </ADrawer>
  <ADrawer
    :open="detailModel !== null"
    title="模型详情"
    :width="isMobile ? '100%' : 460"
    @close="detailModel = null"
  >
    <div v-if="detailModel" role="dialog" aria-label="模型详情" aria-modal="true">
      <h2 class="model-detail__name">{{ detailModel.name }}</h2>
      <dl class="model-detail__fields">
        <div>
          <dt>模型 ID</dt>
          <dd>
            <code>{{ detailModel.id }}</code>
          </dd>
        </div>
        <div>
          <dt>模型类型</dt>
          <dd>{{ modelTypeLabel(detailModel.model_type) }}模型</dd>
        </div>
        <div>
          <dt>状态</dt>
          <dd>
            {{ detailModel.enabled ? (detailModel.published ? '已启用' : '草稿') : '已停用' }}
          </dd>
        </div>
        <div>
          <dt>上游模型</dt>
          <dd>
            <code>{{ detailModel.upstream_model }}</code>
          </dd>
        </div>
        <div v-if="detailModel.model_type === 'chat'">
          <dt>最大输出</dt>
          <dd>{{ detailModel.max_output_tokens.toLocaleString() }} Token</dd>
        </div>
        <div v-if="canReadModels">
          <dt>连接</dt>
          <dd>{{ connectionName(detailModel.connection_id) }}</dd>
        </div>
      </dl>
      <AButton :disabled="!detailModel.id.trim()" @click="copyModelCode(detailModel.id)">
        <CopyOutlined />复制模型代码
      </AButton>
    </div>
  </ADrawer>
  <ModelRatesDialog
    :model="rateModel"
    :can-read="canReadRates"
    :can-write="canWriteRates"
    @close="rateModel = null"
  />
</template>
<style scoped>
.ai-model-center {
  --ai-font-mono: var(--workspace-font-mono);
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  color: var(--color-text);
  background: var(--workspace-bg-page, #f7f8fc);
  font-family: var(--workspace-font-family);
}
.model-workspace {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 20px 24px 28px;
}
.workspace-tabs {
  min-height: 0;
  flex: 1;
}
.workspace-tabs :deep(.ant-tabs-nav) {
  margin: 0 0 20px;
  border-bottom: 1px solid var(--gray-150);
}
.workspace-tabs :deep(.ant-tabs-nav::before) {
  border-bottom: 0;
}
.workspace-tabs :deep(.ant-tabs-tab) {
  height: 40px;
  margin: 0;
  padding: 0 16px;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 400;
}
.workspace-tabs :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) {
  color: var(--main-700);
  font-weight: 600;
}
.workspace-tabs :deep(.ant-tabs-ink-bar) {
  background: var(--main-700);
}
.workspace-tabs :deep(.ant-tabs-content-holder),
.workspace-tabs :deep(.ant-tabs-content),
.workspace-tabs :deep(.ant-tabs-tabpane) {
  min-height: 0;
}
:global(.model-onboarding-drawer .ant-alert) {
  border-radius: 6px;
}
:global(.model-onboarding-drawer) {
  --ai-font-mono: var(--workspace-font-mono);
  font-family: var(--workspace-font-family);
  font-size: 14px;
}
:global(.model-onboarding-drawer .ant-drawer-content) {
  background: var(--bg-elevated);
  color: var(--color-text);
}
:global(.model-onboarding-drawer .ant-drawer-header) {
  margin-bottom: 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-elevated);
  padding-bottom: 16px;
}
:global(.model-onboarding-drawer .ant-drawer-title) {
  color: var(--color-text);
  font-size: 16px;
  font-weight: 600;
}
:global(.model-onboarding-drawer .ant-segmented),
:global(.model-onboarding-drawer .ant-btn) {
  border-radius: 6px;
}
@media (max-width: 760px) {
  :global(.model-onboarding-drawer .ant-drawer-body) {
    padding: 16px;
  }
}
@media (max-width: 640px) {
  .model-workspace {
    padding: 16px;
  }
}
@media (min-width: 761px) {
  .model-workspace {
    padding: 0 var(--workspace-space-6) 28px;
  }
  .workspace-tabs :deep(.ant-tabs-nav) {
    margin-bottom: 12px;
  }
  .ai-model-center :deep(.workspace-toolbar__subtitle) {
    margin-top: 2px;
    font-size: var(--workspace-font-size-helper);
  }
}

.model-catalog-toolbar,
.catalog-tools,
.model-filters,
.view-switch {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-catalog-toolbar {
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin: 4px 0 20px;
}
.model-filters {
  flex-wrap: wrap;
}
.model-filters button {
  padding: 6px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 5px;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.model-filters button.active {
  border-color: var(--main-200);
  background: var(--main-50);
  color: var(--main-700);
  font-weight: 600;
}
.model-filters button:focus-visible {
  outline: 2px solid var(--main-700);
  outline-offset: 2px;
}
.catalog-tools :deep(.ant-input-affix-wrapper) {
  width: 256px;
}
.catalog-total {
  color: var(--color-text-secondary);
  font-size: 13px;
  white-space: nowrap;
}
.view-switch {
  gap: 0;
}
.view-switch :deep(.ant-btn) {
  border-radius: 0;
}
.view-switch :deep(.ant-btn:first-child) {
  border-radius: 5px 0 0 5px;
}
.view-switch :deep(.ant-btn:last-child) {
  border-radius: 0 5px 5px 0;
}
.model-card__details {
  display: flex;
  justify-content: space-between;
  min-width: 0;
  gap: 20px;
  margin: 0;
}
.model-card__details > div {
  min-width: 0;
  flex: 1;
}
.model-card__details > .model-card__output {
  flex: 0 0 auto;
  text-align: right;
}
.model-card__details dt {
  margin-bottom: 3px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 18px;
}
.model-card__details dd {
  overflow: hidden;
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-card__details .model-card__upstream {
  font-family: var(--workspace-font-mono);
  font-size: 12px;
}
.model-card__purpose {
  margin: 12px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 18px;
}
.model-card__connection {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-card__connection-warning {
  display: block;
  color: var(--color-warning-900);
  font-size: 11px;
  line-height: 16px;
}
.model-detail__name {
  margin: 0 0 18px;
  font-size: 18px;
  overflow-wrap: anywhere;
}
.model-detail__fields {
  margin: 0 0 24px;
}
.model-detail__fields > div {
  padding: 14px 0;
  border-bottom: 1px solid var(--gray-150);
}
.model-detail__fields dt {
  margin-bottom: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.model-detail__fields dd {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.model-detail__fields code {
  font-family: var(--workspace-font-mono);
  font-size: 12px;
}
.test-result {
  background: var(--bg-container);
}
.test-result h2 {
  margin: 0;
  font-weight: 500;
}
.test-result pre {
  max-height: 300px;
  overflow: auto;
  padding: 16px;
  border-radius: 5px;
  background: var(--gray-25);
  line-height: 1.7;
}
@media (max-width: 760px) {
  .model-catalog-toolbar {
    gap: 12px;
    margin-bottom: 12px;
  }
  .model-filters {
    gap: 6px;
  }
  .model-filters button {
    padding: 5px 9px;
    font-size: 12px;
  }
  .catalog-tools {
    width: 100%;
  }
  .catalog-tools :deep(.ant-input-affix-wrapper) {
    width: 100%;
  }
  .view-switch {
    display: none;
  }
}
</style>

<style scoped>
.workspace-panel {
  min-width: 0;
  min-height: 360px;
  color: var(--color-text);
}
.panel-toolbar {
  display: flex;
  min-height: 54px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.panel-summary {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.panel-toolbar > :deep(.ant-btn) {
  flex-shrink: 0;
}
.workspace-panel > :deep(.ant-alert) {
  margin-bottom: 14px;
}
.model-table :deep(.ant-table) {
  font-family: var(--workspace-font-family);
  font-size: 14px;
}
.model-table :deep(.ant-table-container) {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.model-table :deep(.ant-table-thead > tr > th) {
  border-bottom-color: var(--gray-150);
  background: var(--gray-10);
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 500;
}
.model-table :deep(.ant-table-tbody > tr > td) {
  border-bottom-color: var(--gray-150);
  background: var(--bg-container);
  color: var(--color-text);
}
.model-table :deep(.ant-table-cell) {
  vertical-align: middle;
}
.model-table :deep(.ant-table-tbody > tr:hover > td) {
  background: var(--gray-50);
}
.model-table :deep(.ant-tag) {
  margin: 0;
}
.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
}
.resource-value {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
}
.mobile-model-cell {
  display: grid;
  min-width: 0;
  gap: 8px;
}
.mobile-model-cell > :deep(.ant-tag) {
  justify-self: start;
}
.mobile-model-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.resource-text {
  min-width: 0;
  overflow: hidden;
  font-family: var(--workspace-font-mono);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-display-name {
  overflow: hidden;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.resource-value--secondary {
  color: var(--color-text-secondary);
}
.copy-button {
  display: inline-flex;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.copy-button:hover {
  background: var(--gray-10);
  color: var(--main-700);
}
.copy-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.copy-button svg {
  width: 14px;
  height: 14px;
}
.muted {
  color: var(--color-text-tertiary);
}
.rate-context {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.rate-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.mobile-actions {
  display: none;
}
:deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (min-width: 761px) {
  .mobile-model-cell {
    gap: 2px;
  }
  .model-table :deep(.ant-table-cell) {
    padding: 12px;
  }
  .model-table :deep(.ant-table-tbody > tr > td) {
    font-size: var(--workspace-font-size-body);
  }
  .model-display-name {
    font-size: 14px;
  }
  .desktop-actions {
    gap: 4px;
    white-space: nowrap;
  }
  .desktop-actions :deep(.ant-btn) {
    height: 28px;
    padding-inline: 2px;
    font-size: 13px;
  }
}
@media (max-width: 760px) {
  .desktop-actions {
    display: none;
  }
  .mobile-actions {
    display: inline-flex;
  }
  :deep(.ant-pagination) {
    justify-content: center;
  }
  .rate-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>

<style scoped>
.onboarding-form {
  color: var(--color-text);
  font-size: 14px;
}
.form-section + .form-section {
  border-top: 1px solid var(--gray-150);
  margin-top: 8px;
  padding-top: 24px;
}
.section-heading {
  margin-bottom: 20px;
}
.section-heading h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
}
.section-heading p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.model-type-options {
  display: flex;
}
.model-type-options :deep(.ant-radio-button-wrapper) {
  flex: 1;
  text-align: center;
}
.field-description {
  margin-top: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.connection-endpoint {
  overflow-wrap: anywhere;
}
.onboarding-form :deep(.ant-input-number) {
  width: 100%;
}
.onboarding-form :deep(.engine-form) {
  padding: 0;
}
.onboarding-form :deep(.ant-form-item-label > label) {
  color: var(--color-text);
}
.advanced-settings {
  margin: 8px 0 16px;
  border: 1px solid var(--gray-150);
  background: transparent;
  border-radius: 6px;
}
.advanced-settings :deep(.ant-collapse-header) {
  color: var(--color-text);
}
.advanced-settings :deep(.ant-collapse-content-box) {
  padding: 16px 20px 0;
  background: var(--bg-container);
}
.status-message {
  margin-top: 16px;
}
.onboarding-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0;
}
.footer-status {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.footer-actions {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
}
</style>

<style scoped>
.footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.test-result {
  margin-top: 20px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.test-result__heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.test-result h2 {
  font-size: 16px;
}
.test-result pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
