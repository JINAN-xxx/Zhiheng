<!-- Adapted from original model center, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
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
import { PlusOutlined, ReloadOutlined, EllipsisOutlined, CopyOutlined } from '@ant-design/icons-vue'
import { useWindowSize } from '@vueuse/core'
import type { TableColumnsType } from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ModelRatesDialog from '../components/ModelRatesDialog.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
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
function setActiveTab(value: unknown): void {
  activeTab.value = typeof value === 'string' ? value : 'platform'
}
const activeTab = ref('platform')
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
  { title: '操作', key: 'actions', width: 160 }
]
const visibleModelColumns = computed<TableColumnsType<ChatModel>>(() =>
  isMobile.value
    ? [
        { title: '模型代码', key: 'model' },
        { title: '操作', key: 'actions', width: 64, align: 'right' }
      ]
    : modelColumns
)
const visibleConnectionColumns = computed(() =>
  isMobile.value
    ? connectionColumns.filter((column) => column.key === 'name' || column.key === 'actions')
    : connectionColumns
)
const connections = ref<ModelConnection[]>([])
const models = ref<ChatModel[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const modalError = ref('')
const connectionOpen = ref(false)
const modelOpen = ref(false)
const editingConnection = ref<ModelConnection | null>(null)
const editingModel = ref<ChatModel | null>(null)
const rateModel = ref<ChatModel | null>(null)
const connectionForm = reactive({ name: '', base_url: '', api_key: '' })
const modelForm = reactive<{
  name: string
  connection_id: string
  upstream_model: string
  max_output_tokens: number | null
  model_type: ChatModel['model_type']
  vision_enabled: boolean
}>({
  name: '',
  connection_id: '',
  upstream_model: '',
  max_output_tokens: 512,
  model_type: 'chat',
  vision_enabled: false
})
const testRun = ref<RunSnapshot | null>(null)
const testingModel = ref('')
const testDisconnected = ref(false)
const scope = new RequestScope()
const mutationScope = new RequestScope()
const testScope = new RequestScope()
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
  try {
    const [items, availableModels] = await Promise.all([
      getConnections(context.signal),
      getModels(context.signal, true)
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
  try {
    await navigator.clipboard.writeText(value)
  } catch {
    error.value = '复制失败，请手动复制模型代码'
  }
}
function openConnection(connection: ModelConnection | null = null): void {
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
  if (saving.value) return
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
  if (saving.value) return
  const context = mutationScope.replace()
  const execute = async () => {
    if (!context.isCurrent()) return
    saving.value = true
    error.value = ''
    try {
      await controlModel(model.id, action, context.signal)
      if (context.isCurrent()) await load()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  if (action === 'delete' || action === 'disable' || action === 'unpublish')
    Modal.confirm({
      title:
        action === 'delete'
          ? '删除该模型？有历史或业务引用时将拒绝删除。'
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
  if (saving.value) return
  const context = mutationScope.replace()
  const execute = async () => {
    if (!context.isCurrent()) return
    saving.value = true
    error.value = ''
    try {
      await controlConnection(connection.id, action, context.signal)
      if (context.isCurrent()) await load()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  if (action === 'enable') await execute()
  else
    Modal.confirm({
      title:
        action === 'delete'
          ? '删除该凭据？模型或历史引用存在时将拒绝删除。'
          : '停用后，新请求将不能使用此连接。是否继续？',
      okText: '确定',
      cancelText: '取消',
      onOk: execute
    })
}
async function saveModel(): Promise<void> {
  if (saving.value) return
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
  if (testBusy.value) return
  const context = testScope.replace()
  testingModel.value = model.id
  testRun.value = null
  testDisconnected.value = false
  error.value = ''
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
  if (!testRun.value) return
  const context = testScope.replace()
  error.value = ''
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
  if (!testRun.value) return
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
    <WorkspaceToolbar
      title="模型目录"
      :subtitle="
        isMobile
          ? ''
          : activeTab === 'platform'
            ? `${models.length} 个平台模型`
            : `${connections.length} 个服务商凭据`
      "
      ><template #actions
        ><AButton shape="circle" aria-label="刷新当前视图" :loading="loading" @click="load"
          ><ReloadOutlined /></AButton
        ><AButton
          v-if="activeTab === 'platform' || isMobile"
          type="primary"
          aria-label="接入模型"
          :disabled="!connections.length"
          @click="openModel()"
          ><PlusOutlined />接入模型</AButton
        ><AButton v-else type="primary" aria-label="新增凭据" @click="openConnection()"
          ><PlusOutlined />新增凭据</AButton
        ></template
      ></WorkspaceToolbar
    >
    <section class="model-workspace">
      <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" />
      <ATabs :active-key="activeTab" class="workspace-tabs" @update:active-key="setActiveTab">
        <ATabPane key="platform" tab="平台模型"
          ><section class="workspace-panel">
            <div v-if="isMobile" class="panel-toolbar">
              <div class="panel-summary">
                <span>{{ models.length }} 个平台模型</span
                ><span v-if="lastSuccessAt">更新于 {{ lastSuccessAt }}</span>
              </div>
              <AButton shape="circle" :loading="loading" aria-label="刷新平台模型" @click="load"
                ><ReloadOutlined
              /></AButton>
            </div>
            <ResourceTable
              v-if="models.length || loading"
              class="model-table"
              :columns="visibleModelColumns"
              :scroll-x="isMobile ? '100%' : 850"
              :rows="models"
              :loading="loading"
              ><template #bodyCell="{ column, record: model }">
                <template v-if="column.key === 'model'"
                  ><div class="mobile-model-cell">
                    <span v-if="!isMobile" class="model-display-name">{{ model.name }}</span>
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
                      ><ATag color="success">已配置</ATag
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
                  ><ATag color="success">已配置</ATag></template
                >
                <template v-else-if="column.key === 'status'"
                  ><ATag :color="model.enabled ? 'success' : 'default'">{{
                    model.enabled ? (model.published ? '已启用' : '草稿') : '已停用'
                  }}</ATag></template
                >
                <template v-else-if="column.key === 'actions'"
                  ><div class="row-actions desktop-actions">
                    <AButton
                      type="link"
                      size="small"
                      aria-label="编辑模型"
                      @click="openModel(model)"
                      >编辑</AButton
                    >
                    <AButton
                      type="link"
                      size="small"
                      aria-label="费率配置"
                      @click="rateModel = model"
                      >费率</AButton
                    >
                    <AButton
                      type="link"
                      size="small"
                      aria-label="测试连接"
                      :loading="testingModel === model.id && testBusy"
                      :disabled="testBusy || !model.enabled"
                      @click="testModel(model)"
                      >测试</AButton
                    >
                  </div>
                  <ADropdown :trigger="['click']"
                    ><AButton type="text" aria-label="更多操作"><EllipsisOutlined /></AButton
                    ><template #overlay
                      ><AMenu
                        ><AMenuItem @click="openModel(model)">编辑模型</AMenuItem
                        ><AMenuItem @click="rateModel = model">费率配置</AMenuItem
                        ><AMenuItem :disabled="testBusy || !model.enabled" @click="testModel(model)"
                          >测试连接</AMenuItem
                        ><AMenuItem
                          @click="changeModel(model, model.enabled ? 'disable' : 'enable')"
                          >{{ model.enabled ? '停用' : '启用' }}</AMenuItem
                        ><AMenuItem
                          v-if="model.enabled"
                          @click="changeModel(model, model.published ? 'unpublish' : 'publish')"
                          >{{ model.published ? '取消发布' : '发布' }}</AMenuItem
                        ><AMenuItem
                          v-if="!model.enabled"
                          danger
                          @click="changeModel(model, 'delete')"
                          >删除模型</AMenuItem
                        ></AMenu
                      ></template
                    ></ADropdown
                  ></template
                >
              </template></ResourceTable
            >
            <WorkspaceState
              v-else
              title="暂无平台模型"
              :message="
                connections.length
                  ? '点击接入模型，选择已保存的连接。'
                  : '请先在凭据管理中添加模型连接。'
              "
            /></section
        ></ATabPane>
        <ATabPane key="credentials" tab="凭据管理"
          ><section class="workspace-panel">
            <div v-if="isMobile" class="panel-toolbar">
              <div class="panel-summary">
                <span v-if="lastSuccessAt">更新于 {{ lastSuccessAt }}</span>
              </div>
              <AButton type="primary" aria-label="新增凭据" @click="openConnection()"
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
                  ><code>{{ connection.name }}</code
                  ><ATag v-if="isMobile">{{
                    connection.enabled ? '已启用' : '已停用'
                  }}</ATag></template
                ><template v-else-if="column.key === 'provider'">OpenAI 兼容</template
                ><template v-else-if="column.key === 'status'"
                  ><ATag :color="connection.has_api_key ? 'success' : 'warning'">{{
                    connection.has_api_key ? '密钥已配置' : '未配置密钥'
                  }}</ATag></template
                ><template v-else-if="column.key === 'actions'"
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
                        ><AMenuItem
                          v-if="!connection.enabled"
                          danger
                          @click="changeConnection(connection, 'delete')"
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
              message="添加 OpenAI 兼容的模型服务连接。"
            /></section></ATabPane
      ></ATabs>
      <section v-if="testRun" class="test-result">
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
    :open="connectionOpen"
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
    :open="modelOpen"
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
        ></AForm>
      </section>
    </div>
    <template #footer
      ><div class="onboarding-footer">
        <span class="footer-status">保存后可在模型目录中测试连接。</span>
        <div class="footer-actions">
          <AButton @click="closeModel">取消</AButton
          ><AButton type="primary" :loading="saving" @click="saveModel">保存模型</AButton>
        </div>
      </div></template
    >
  </ADrawer>
  <ModelRatesDialog :model="rateModel" @close="rateModel = null" />
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
  background: var(--bg-container);
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
  font-weight: 500;
}
.workspace-tabs :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) {
  color: var(--main-700);
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
  :global(.model-onboarding-drawer) {
    width: 100% !important;
  }
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
    padding: 12px var(--workspace-space-6) 24px;
  }
  .workspace-tabs :deep(.ant-tabs-nav) {
    margin-bottom: 12px;
  }
  .ai-model-center :deep(.workspace-toolbar__subtitle) {
    margin-top: 2px;
    font-size: var(--workspace-font-size-helper);
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
  background: var(--gray-50) !important;
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
  font-size: 15px;
  font-weight: 600;
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
  color: var(--color-text) !important;
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
