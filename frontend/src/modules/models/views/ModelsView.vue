<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Empty as AEmpty,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  InputPassword as AInputPassword,
  Modal as AModal,
  Select as ASelect,
  Spin as ASpin,
  Tag as ATag
} from 'ant-design-vue'
import {
  ApiOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined
} from '@ant-design/icons-vue'
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
  startModelTest
} from '../api/index.ts'
import type { ChatModel, ModelConnection } from '../api/index.ts'
const connections = ref<ModelConnection[]>([])
const models = ref<ChatModel[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const modalError = ref('')
const connectionOpen = ref(false)
const modelOpen = ref(false)
const connectionForm = reactive({ name: '', base_url: '', api_key: '' })
const modelForm = reactive<{
  name: string
  connection_id: string
  upstream_model: string
  max_output_tokens: number | null
}>({
  name: '',
  connection_id: '',
  upstream_model: '',
  max_output_tokens: 512
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
  connections.value.map((item) => ({ value: item.id, label: item.name }))
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
      getModels(context.signal)
    ])
    if (context.isCurrent()) {
      connections.value = items
      models.value = availableModels
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
function openConnection(): void {
  connectionOpen.value = true
  modalError.value = ''
}
function openModel(): void {
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
  if (!connectionForm.name.trim() || !connectionForm.base_url.trim() || !connectionForm.api_key) {
    modalError.value = '请填写连接名称、API 地址和密钥'
    return
  }
  const context = mutationScope.replace()
  saving.value = true
  modalError.value = ''
  try {
    const connection = await createConnection(
      {
        name: connectionForm.name.trim(),
        base_url: connectionForm.base_url.trim(),
        api_key: connectionForm.api_key
      },
      context.signal
    )
    if (context.isCurrent()) {
      connections.value.push(connection)
      connectionOpen.value = false
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
    maxOutputTokens > 8192
  ) {
    modalError.value = '请填写模型信息，输出上限须为 1–8192 的整数'
    return
  }
  const context = mutationScope.replace()
  saving.value = true
  modalError.value = ''
  try {
    const model = await createModel(
      {
        ...modelForm,
        max_output_tokens: maxOutputTokens,
        name: modelForm.name.trim(),
        upstream_model: modelForm.upstream_model.trim()
      },
      context.signal
    )
    if (context.isCurrent()) {
      models.value.push(model)
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
  <section class="content-page">
    <div class="page-heading">
      <div>
        <p class="eyebrow">MODEL WORKSPACE</p>
        <h1>模型管理</h1>
        <p class="muted">连接你的模型服务，让每次对话都有合适的选择。</p>
      </div>
      <AButton @click="load"><ReloadOutlined /> 刷新</AButton>
    </div>
    <AAlert v-if="error" :message="error" type="error" show-icon class="space-bottom" /><ASpin
      :spinning="loading"
    >
      <section class="settings-section">
        <div class="section-title">
          <div>
            <h2>
              模型连接 <span class="count">{{ connections.length }}</span>
            </h2>
            <p class="muted small">OpenAI 兼容的 API 服务</p>
          </div>
          <AButton @click="openConnection"><PlusOutlined /> 添加连接</AButton>
        </div>
        <div v-if="connections.length" class="connection-grid">
          <article v-for="connection in connections" :key="connection.id" class="connection-card">
            <div class="connection-icon"><ApiOutlined /></div>
            <div>
              <h3>{{ connection.name }}</h3>
              <p class="muted small break-word">{{ connection.base_url }}</p>
              <ATag :color="connection.has_api_key ? 'success' : 'warning'">{{
                connection.has_api_key ? '密钥已配置' : '未配置密钥'
              }}</ATag>
            </div>
          </article>
        </div>
        <AEmpty v-else description="先添加一个模型连接" :image="AEmpty.PRESENTED_IMAGE_SIMPLE" />
      </section>
      <section class="settings-section">
        <div class="section-title">
          <div>
            <h2>
              可用模型 <span class="count">{{ models.length }}</span>
            </h2>
            <p class="muted small">当前支持文本对话与流式输出</p>
          </div>
          <AButton type="primary" :disabled="!connections.length" @click="openModel"
            ><PlusOutlined /> 添加模型</AButton
          >
        </div>
        <div v-if="models.length" class="model-grid">
          <article v-for="model in models" :key="model.id" class="model-card">
            <div class="model-card-heading">
              <div class="model-icon"><ThunderboltOutlined /></div>
              <ATag :color="model.enabled ? 'success' : 'default'">{{
                model.enabled ? '已启用' : '已停用'
              }}</ATag>
            </div>
            <h3>{{ model.name }}</h3>
            <p class="muted small break-word">{{ model.upstream_model }}</p>
            <div class="model-details">
              <span>连接</span
              ><strong>{{
                connections.find((item) => item.id === model.connection_id)?.name || '—'
              }}</strong
              ><span>输出上限</span
              ><strong>{{ model.max_output_tokens.toLocaleString() }} tokens</strong>
            </div>
            <div class="model-card-footer">
              <span class="capability-chip">文本 · 流式</span
              ><AButton
                size="small"
                :loading="testingModel === model.id && testBusy"
                :disabled="testBusy || !model.enabled"
                @click="testModel(model)"
                >测试连接</AButton
              >
            </div>
          </article>
        </div>
        <AEmpty
          v-else
          description="添加模型后，即可开始对话"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        />
      </section>
      <section v-if="testRun" class="test-result">
        <div class="section-title">
          <h2>
            连接测试 <ATag>{{ runStatusLabel[testRun.status] }}</ATag>
          </h2>
          <div>
            <AButton v-if="testDisconnected" @click="resumeTest">恢复查看</AButton
            ><AButton v-if="!isTerminal(testRun)" @click="stopTest">停止测试</AButton>
          </div>
        </div>
        <pre>{{ testRun.text || '等待模型响应…' }}</pre>
        <p v-if="testRun.error" class="run-error">{{ testRun.error.message }}</p>
        <p class="muted small">
          连接测试会计入用量。{{
            testRun.usage.status === 'known'
              ? `输入 ${testRun.usage.input_tokens ?? '待核对'} · 输出 ${testRun.usage.output_tokens ?? '待核对'} tokens`
              : '用量待核对'
          }}
        </p>
      </section>
    </ASpin>
  </section>
  <AModal
    v-model:open="connectionOpen"
    title="添加模型连接"
    ok-text="保存连接"
    cancel-text="取消"
    :confirm-loading="saving"
    :destroy-on-close="true"
    @ok="saveConnection"
    @cancel="closeConnection"
    ><AAlert
      v-if="modalError"
      :message="modalError"
      type="error"
      show-icon
      class="space-bottom" /><AForm layout="vertical"
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
      ><AFormItem label="API Key" required
        ><AInputPassword
          v-model:value="connectionForm.api_key"
          autocomplete="off"
          placeholder="密钥仅用于本次提交，保存后不会回显" /></AFormItem></AForm
  ></AModal>
  <AModal
    v-model:open="modelOpen"
    title="添加模型"
    ok-text="保存模型"
    cancel-text="取消"
    :confirm-loading="saving"
    :destroy-on-close="true"
    @ok="saveModel"
    @cancel="closeModel"
    ><AAlert
      v-if="modalError"
      :message="modalError"
      type="error"
      show-icon
      class="space-bottom" /><AForm layout="vertical"
      ><AFormItem label="显示名称" required
        ><AInput
          v-model:value="modelForm.name"
          placeholder="例如：日常助手"
          :maxlength="80" /></AFormItem
      ><AFormItem label="模型连接" required
        ><ASelect
          :value="modelForm.connection_id"
          :options="connectionOptions"
          placeholder="选择模型连接"
          @update:value="setConnectionId" /></AFormItem
      ><AFormItem label="上游模型标识" required
        ><AInput
          v-model:value="modelForm.upstream_model"
          placeholder="填写服务商提供的模型 ID"
          :maxlength="200" /></AFormItem
      ><AFormItem label="最大输出 tokens" required
        ><AInputNumber
          :value="modelForm.max_output_tokens ?? ''"
          :min="1"
          :max="8192"
          :precision="0"
          class="full-width"
          @update:value="setMaxOutputTokens" /></AFormItem></AForm
  ></AModal>
</template>
