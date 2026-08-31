<template>
  <a-drawer
    v-model:open="visible"
    title="接入模型"
    width="min(760px, 100vw)"
    destroy-on-close
    root-class-name="model-onboarding-drawer"
    @close="invalidateRequests"
  >
    <a-spin :spinning="bootLoading">
      <div class="onboarding-body">
        <a-alert v-if="bootError" :message="bootError" type="error" :closable="false" show-icon>
          <template #description><a-button type="link" @click="loadOptions">重新加载</a-button></template>
        </a-alert>

        <template v-else>
          <a-steps :current="activeStep" :items="workflowSteps" responsive class="workflow-steps" />

          <a-alert v-if="failureMessage" message="接入未完成" type="error" :closable="false" show-icon class="workflow-error">
            <template #description>
              <div class="workflow-error-content">
                <span>{{ failureMessage }}</span>
                <div>
                  <a-button type="link" @click="retry"
                    ><template #icon><ReloadOutlined /></template>重试当前步骤</a-button
                  >
                  <a-button type="link" @click="visible = false">关闭</a-button>
                </div>
              </div>
            </template>
          </a-alert>

          <section v-if="workflowState === 'credential'" class="step-section">
            <div class="section-heading">
              <div>
                <h3>连接信息</h3>
                <p>选择模型服务商，并填写访问该服务所需的凭据。</p>
              </div>
            </div>
            <a-form layout="vertical">
              <a-form-item label="服务商">
                <a-select
                  v-model:value="selectedProvider"
                  show-search
                  option-filter-prop="label"
                  placeholder="选择服务商"
                  :options="providerOptions"
                />
              </a-form-item>
            </a-form>
            <div v-if="selectedProvider" class="credential-choice">
              <span class="choice-label">凭据方式</span>
              <a-segmented v-model:value="credentialMode" :options="credentialModes" />
            </div>
            <template v-if="credentialMode === 'existing'">
              <a-form layout="vertical">
                <a-form-item label="选择已保存凭据">
                  <a-select
                    v-model:value="selectedCredential"
                    show-search
                    option-filter-prop="label"
                    placeholder="选择现有凭据"
                    :options="credentialOptions"
                  />
                </a-form-item>
              </a-form>
              <div class="step-actions">
                <a-button type="primary" :disabled="!selectedCredential" @click="useCredential"
                  >下一步<template #icon><ArrowRightOutlined /></template
                ></a-button>
              </div>
            </template>
            <CredentialForm
              v-else
              quick
              :providers="providers"
              :credential-name="autoCredentialName"
              :provider="selectedProvider"
              :submitting="submitting"
              @submit="createCredential"
              @cancel="visible = false"
            />
          </section>

          <section v-if="workflowState === 'deployment'" class="step-section">
            <div class="resource-strip">
              <span>{{ selectedProviderLabel }}</span>
              <a-tag color="success">凭据已就绪</a-tag>
            </div>
            <DeploymentForm
              quick
              :providers="providers"
              :credentials="credentials"
              :initial-alias="autoDeploymentAlias"
              :initial-credential-name="resources.credential?.name || ''"
              :initial-provider="resources.credential?.providerId || resources.credential?.provider || ''"
              :submitting="submitting"
              :test-loading="testLoading"
              :tested="connectionTested"
              require-test
              @change="connectionTested = false"
              @test="testQuickConnection"
              @submit="createQuickDeployment"
              @cancel="visible = false"
            />
          </section>

          <section v-if="workflowState === 'catalog'" class="step-section">
            <div class="resource-strip">
              <span>{{ resources.deployment?.baseModel }}</span>
              <a-tag color="success">连接已验证</a-tag>
            </div>
            <div class="section-heading">
              <div>
                <h3>确认接入</h3>
                <p>确认用户在平台中看到的模型名称。</p>
              </div>
            </div>
            <a-form ref="catalogFormRef" :model="catalogForm" :rules="catalogRules" layout="vertical">
              <div class="catalog-grid">
                <a-form-item label="显示名称" name="displayName">
                  <a-input v-model:value="catalogForm.displayName" placeholder="例如：专业对话模型" :disabled="Boolean(resources.model)" />
                </a-form-item>
                <a-form-item label="模型代码" name="modelCode">
                  <a-input v-model:value="catalogForm.modelCode" placeholder="自动生成，可调整" :disabled="Boolean(resources.model)" />
                </a-form-item>
              </div>
              <a-collapse class="advanced-settings">
                <a-collapse-panel key="catalog-advanced" header="高级设置">
                  <div class="catalog-grid">
                    <a-form-item label="模型类型" name="modelType">
                      <a-segmented
                        v-model:value="catalogForm.modelType"
                        :options="modelTypes"
                        :disabled="Boolean(resources.model)"
                        @change="applyTypedDefaults"
                      />
                    </a-form-item>
                  </div>
                  <div v-if="catalogForm.modelType !== 'chat'" class="catalog-grid">
                    <a-form-item v-if="catalogForm.modelType === 'embedding'" label="默认向量维度">
                      <a-input-number
                        v-model:value="catalogForm.defaultParameters.dimensions"
                        :min="1"
                        :max="65536"
                        :disabled="Boolean(resources.model)"
                        placeholder="可选"
                      />
                    </a-form-item>
                    <a-form-item v-if="catalogForm.modelType === 'rerank'" label="默认 Top N">
                      <a-input-number
                        v-model:value="catalogForm.defaultParameters.topN"
                        :min="1"
                        :max="256"
                        :disabled="Boolean(resources.model)"
                        placeholder="可选"
                      />
                    </a-form-item>
                    <a-form-item v-if="catalogForm.modelType === 'rerank'" label="默认返回文档">
                      <a-switch v-model:checked="catalogForm.defaultParameters.returnDocuments" :disabled="Boolean(resources.model)" />
                    </a-form-item>
                  </div>
                </a-collapse-panel>
              </a-collapse>
              <div class="step-actions">
                <a-button type="primary" :loading="submitting" @click="publish"
                  ><template #icon><SendOutlined /></template>接入并发布</a-button
                >
              </div>
            </a-form>
          </section>

          <section v-if="workflowState === 'published'" class="result-section">
            <a-result status="success" title="接入成功" :sub-title="resources.model?.displayName || resources.model?.modelCode || ''">
              <template #extra
                ><a-button type="primary" @click="visible = false"
                  ><template #icon><CheckOutlined /></template>完成</a-button
                ></template
              >
            </a-result>
            <div class="retained-resources">
              <div class="retained-row">
                <span>平台模型</span><code>{{ resources.model?.modelCode }}</code>
              </div>
            </div>
          </section>
        </template>
      </div>
    </a-spin>
  </a-drawer>
</template>

<script setup>
import { ArrowRightOutlined, CheckOutlined, ReloadOutlined, SendOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback.js'
import {
  createEngineCredential,
  createEngineDeployment,
  listEngineCredentials,
  listEngineDeployments,
  listEngineProviders,
  testEngineConnection,
} from '../../../api/modelEngine.js'
import {
  createPlatformModel,
  getPlatformModel,
  listPlatformModels,
  publishPlatformModel,
  savePlatformModelBinding,
} from '../../../api/modelCatalog.js'
import {
  buildQuickResourceIdentity,
  collectAllPages,
  createRequestGate,
  curateModelProviders,
  defaultModelCapabilities,
  getQuickOnboardingStep,
  isCredentialCompatible,
  isManagementMisconfiguredError,
  normalizeCredential,
  normalizeDeployment,
  normalizePlatformModel,
  normalizeProvider,
  reconcileDeployment,
  resolveProvider,
  shouldUpdateBinding,
} from '../../../utils/modelEngine.js'
import CredentialForm from './CredentialForm.vue'
import DeploymentForm from './DeploymentForm.vue'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'completed'])
const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

const workflowState = ref('credential')
const failedStep = ref('credential')
const failureMessage = ref('')
const bootLoading = ref(false)
const bootError = ref('')
const submitting = ref(false)
const testLoading = ref(false)
const connectionTested = ref(false)
const providers = ref([])
const credentials = ref([])
const models = ref([])
const selectedProvider = ref('')
const credentialMode = ref('existing')
const selectedCredential = ref('')
const resourceSuffix = ref('local')
const catalogMode = ref('new')
const catalogFormRef = ref()
const resources = reactive({
  credential: null,
  deployment: null,
  model: null,
  binding: null,
})
const requestGate = createRequestGate()
const catalogForm = reactive({
  existingModelId: '',
  modelCode: '',
  displayName: '',
  modelType: 'chat',
  defaultParameters: {
    temperature: null,
    topP: null,
    maxTokens: null,
    dimensions: null,
    topN: null,
    returnDocuments: false,
  },
})

const credentialModes = [
  { label: '使用现有', value: 'existing' },
  { label: '新建凭据', value: 'new' },
]
const workflowSteps = [{ title: '连接信息' }, { title: '测试模型' }, { title: '确认接入' }]
const modelTypes = [
  { label: '对话', value: 'chat' },
  { label: '向量', value: 'embedding' },
  { label: '排序', value: 'rerank' },
]
const catalogRules = {
  existingModelId: [{ required: true, message: '请选择平台模型草稿', trigger: 'change' }],
  modelCode: [
    { required: true, message: '模型代码不能为空', trigger: 'blur' },
    {
      pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/,
      message: '模型代码格式不正确',
      trigger: 'blur',
    },
  ],
  displayName: [{ required: true, message: '显示名称不能为空', trigger: 'blur' }],
  modelType: [{ required: true, message: '请选择模型类型', trigger: 'change' }],
}

const activeStep = computed(() => getQuickOnboardingStep(workflowState.value).index)
const selectedProviderRecord = computed(() => resolveProvider(providers.value, selectedProvider.value))
const selectedProviderLabel = computed(() => selectedProviderRecord.value?.displayName || selectedProvider.value)
const compatibleCredentials = computed(() =>
  credentials.value.filter((credential) => isCredentialCompatible(credential, selectedProvider.value, providers.value)),
)
const providerOptions = computed(() =>
  providers.value.map((item) => ({
    label: item.displayName,
    value: item.provider,
  })),
)
const credentialOptions = computed(() =>
  compatibleCredentials.value.map((item) => ({
    label: item.name,
    value: item.name,
  })),
)
const quickIdentity = (upstreamModel = '') =>
  buildQuickResourceIdentity({
    provider: selectedProvider.value,
    upstreamModel,
    suffix: resourceSuffix.value,
  })
const autoCredentialName = computed(() => quickIdentity().credentialName)
const autoDeploymentAlias = computed(() => quickIdentity().deploymentAlias)

function createResourceSuffix() {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return uuid.replaceAll('-', '').slice(0, 8)
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`.slice(0, 8)
}

function resetWorkflow() {
  workflowState.value = 'credential'
  failedStep.value = 'credential'
  failureMessage.value = ''
  bootLoading.value = false
  submitting.value = false
  testLoading.value = false
  selectedProvider.value = ''
  credentialMode.value = 'existing'
  selectedCredential.value = ''
  catalogMode.value = 'new'
  resourceSuffix.value = createResourceSuffix()
  connectionTested.value = false
  resources.credential = null
  resources.deployment = null
  resources.model = null
  resources.binding = null
  Object.assign(catalogForm, {
    existingModelId: '',
    modelCode: '',
    displayName: '',
    modelType: 'chat',
    defaultParameters: {
      temperature: null,
      topP: null,
      maxTokens: null,
      dimensions: null,
      topN: null,
      returnDocuments: false,
    },
  })
}

function applyTypedDefaults() {
  if (resources.model) return
  catalogForm.defaultParameters = {
    temperature: null,
    topP: null,
    maxTokens: null,
    dimensions: null,
    topN: null,
    returnDocuments: false,
  }
}

async function loadOptions() {
  const requestGeneration = requestGate.begin()
  bootLoading.value = true
  bootError.value = ''
  try {
    const [providerResponse, credentialResponse, modelRows] = await Promise.all([
      listEngineProviders(),
      listEngineCredentials(),
      collectAllPages(({ pageNum, pageSize }) => listPlatformModels({ pageNum, pageSize }), { pageSize: 100 }),
    ])
    if (!requestGate.isCurrent(requestGeneration)) return
    providers.value = curateModelProviders((providerResponse?.data || []).map(normalizeProvider).filter(Boolean))
    credentials.value = (credentialResponse?.data || []).map(normalizeCredential)
    models.value = modelRows.map(normalizePlatformModel)
    selectedProvider.value = providers.value[0]?.provider || ''
  } catch (event) {
    if (requestGate.isCurrent(requestGeneration))
      bootError.value = isManagementMisconfiguredError(event) ? '管理密钥未配置' : '接入资源加载失败'
  } finally {
    if (requestGate.isCurrent(requestGeneration)) bootLoading.value = false
  }
}

function fail(step, message) {
  failedStep.value = step
  failureMessage.value = message
}

function useCredential() {
  const credential = compatibleCredentials.value.find((item) => item.name === selectedCredential.value)
  if (!credential) return
  resources.credential = { ...credential, providerId: selectedProvider.value }
  workflowState.value = 'deployment'
}

async function createCredential(payload) {
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  submitting.value = true
  try {
    await createEngineCredential(payload)
    if (!requestGate.isCurrent(requestGeneration)) return
    const provider = resolveProvider(providers.value, payload.provider)
    resources.credential = {
      name: payload.credentialName,
      provider: provider?.engineProvider || '',
      providerId: provider?.provider || payload.provider,
      deploymentCount: 0,
      createdAt: null,
      updatedAt: null,
    }
    credentials.value.push(resources.credential)
    workflowState.value = 'deployment'
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('credential', '连接信息保存失败，当前输入已保留')
  } finally {
    if (requestGate.isCurrent(requestGeneration)) submitting.value = false
  }
}

async function testConnection(payload) {
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  try {
    await feedback.confirm('连接测试可能触发服务商请求并产生极小费用，确认继续？')
  } catch {
    return
  }
  if (!requestGate.isCurrent(requestGeneration)) return
  testLoading.value = true
  try {
    const response = await testEngineConnection(payload)
    if (!requestGate.isCurrent(requestGeneration)) return
    if (!response?.data?.success) throw new Error('connection rejected')
    connectionTested.value = true
    feedback.msgSuccess(`连接成功${response.data.latencyMs != null ? `，${response.data.latencyMs} ms` : ''}`)
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('deployment', '模型连接测试失败，当前输入已保留')
  } finally {
    if (requestGate.isCurrent(requestGeneration)) testLoading.value = false
  }
}

async function lookupDeployment(payload) {
  const response = await listEngineDeployments()
  const deployments = (response?.data || []).map(normalizeDeployment)
  return reconcileDeployment(deployments, payload, providers.value)
}

function reuseDeployment(deployment) {
  resources.deployment = deployment
  const identity = quickIdentity(deployment.baseModel)
  catalogForm.modelCode = identity.modelCode
  catalogForm.displayName = identity.displayName
  failureMessage.value = ''
  workflowState.value = 'catalog'
}

function testQuickConnection(payload) {
  return testConnection(payload)
}

function createQuickDeployment(payload) {
  return createDeployment({
    ...payload,
    modelAlias: quickIdentity(payload.upstreamModel).deploymentAlias,
  })
}

async function createDeployment(payload) {
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  submitting.value = true
  let beforeCreate
  try {
    beforeCreate = await lookupDeployment(payload)
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('deployment', '接入前核对失败，未发起创建；当前输入已保留')
    if (requestGate.isCurrent(requestGeneration)) submitting.value = false
    return
  }
  if (!requestGate.isCurrent(requestGeneration)) return
  if (beforeCreate.kind === 'match') {
    reuseDeployment(beforeCreate.deployment)
    submitting.value = false
    return
  }
  if (beforeCreate.kind === 'conflict') {
    fail('deployment', '自动生成的调用别名已被其他配置占用，请关闭后重新接入')
    submitting.value = false
    return
  }
  try {
    const response = await createEngineDeployment(payload)
    if (!requestGate.isCurrent(requestGeneration)) return
    const deployment = normalizeDeployment(response?.data)
    const createdResult = reconcileDeployment([deployment], payload, providers.value)
    if (deployment.deploymentId && createdResult.kind === 'match') {
      reuseDeployment(createdResult.deployment)
    } else {
      fail('deployment', '模型接入响应与提交配置不一致，已停止继续；当前输入已保留')
    }
  } catch {
    if (!requestGate.isCurrent(requestGeneration)) return
    try {
      const afterCreate = await lookupDeployment(payload)
      if (!requestGate.isCurrent(requestGeneration)) return
      if (afterCreate.kind === 'match') {
        reuseDeployment(afterCreate.deployment)
        feedback.msgWarning('创建响应结果未知，已核对模型实际存在并继续')
      } else if (afterCreate.kind === 'conflict') {
        fail('deployment', '创建结果未知；已核对到同名但配置不一致的模型，请检查后重试')
      } else {
        fail('deployment', '模型接入失败；已核对未发现匹配资源，可重试当前步骤')
      }
    } catch {
      if (requestGate.isCurrent(requestGeneration)) fail('deployment', '创建结果未知且核对失败；重试时将先重新核对')
    }
  } finally {
    if (requestGate.isCurrent(requestGeneration)) submitting.value = false
  }
}

function defaultParametersPayload() {
  const type = catalogForm.modelType
  if (type === 'embedding') {
    return { dimensions: catalogForm.defaultParameters.dimensions || null }
  }
  if (type === 'rerank') {
    return {
      topN: catalogForm.defaultParameters.topN || null,
      returnDocuments: catalogForm.defaultParameters.returnDocuments || null,
    }
  }
  return {}
}

async function ensureCatalogModel(requestGeneration) {
  if (resources.model) return resources.model
  if (catalogMode.value === 'existing') {
    const detail = await getPlatformModel(catalogForm.existingModelId)
    if (!requestGate.isCurrent(requestGeneration)) return null
    resources.model = detail?.data
    resources.binding = detail?.data?.binding || null
    return resources.model
  }
  const response = await createPlatformModel({
    modelCode: catalogForm.modelCode,
    displayName: catalogForm.displayName,
    modelType: catalogForm.modelType,
    capabilities: defaultModelCapabilities(catalogForm.modelType),
    defaultParameters: defaultParametersPayload(),
  })
  if (!requestGate.isCurrent(requestGeneration)) return null
  resources.model = response?.data
  models.value.push(normalizePlatformModel(resources.model))
  return resources.model
}

async function publish() {
  const fields = ['modelCode', 'displayName', 'modelType']
  const valid = await catalogFormRef.value
    .validateFields(fields)
    .then(() => true)
    .catch(() => false)
  if (!valid) return
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  submitting.value = true
  try {
    const model = await ensureCatalogModel(requestGeneration)
    if (!requestGate.isCurrent(requestGeneration) || !model) return
    if (shouldUpdateBinding(resources.binding, resources.deployment.alias)) {
      const bindingPayload = {
        engineResourceId: resources.deployment.alias,
        bindingStatus: 'active',
        engineMetadata: {},
      }
      if (Number.isInteger(resources.binding?.version) && resources.binding.version > 0) {
        bindingPayload.expectedVersion = resources.binding.version
      }
      const response = await savePlatformModelBinding(model.modelId, bindingPayload)
      if (!requestGate.isCurrent(requestGeneration)) return
      resources.binding = response?.data
    }
    const response = await publishPlatformModel(model.modelId)
    if (!requestGate.isCurrent(requestGeneration)) return
    resources.model = response?.data || model
    workflowState.value = 'published'
    emit('completed')
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('catalog', '确认接入失败，已完成的资源和当前输入均已保留')
  } finally {
    if (requestGate.isCurrent(requestGeneration)) submitting.value = false
  }
}

function retry() {
  workflowState.value = failedStep.value
  failureMessage.value = ''
}
function invalidateRequests() {
  requestGate.invalidate()
}

watch(selectedProvider, () => {
  selectedCredential.value = ''
  resources.credential = null
  connectionTested.value = false
  credentialMode.value = compatibleCredentials.value.length > 0 ? 'existing' : 'new'
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) {
      invalidateRequests()
      return
    }
    requestGate.activate()
    resetWorkflow()
    loadOptions()
  },
)
onBeforeUnmount(invalidateRequests)
</script>

<style scoped>
.onboarding-body {
  min-height: 460px;
  color: var(--color-text);
  font-size: 13px;
}
.workflow-steps {
  margin: 4px 0 28px;
}
.workflow-steps :deep(.ant-steps-item-title) { color: var(--color-text-secondary); }
.workflow-steps :deep(.ant-steps-item-process .ant-steps-item-title) { color: var(--main-700); }
.workflow-steps :deep(.ant-steps-item-tail::after) { background: var(--gray-150); }
.workflow-error {
  margin-bottom: 18px;
}
.workflow-error-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}
.step-section {
  min-width: 0;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.section-heading h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0;
}
.section-heading p {
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}
.credential-choice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.choice-label {
  color: var(--color-text);
  font-weight: 500;
}
.resource-strip,
.retained-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  border-top: 1px solid var(--gray-150);
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-container);
  padding: 10px 0;
  margin-bottom: 18px;
}
.resource-strip code,
.retained-row code {
  min-width: 0;
  overflow: hidden;
  font-family: var(--ai-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.strip-alias {
  margin-left: auto;
  color: var(--color-text-tertiary);
}
.catalog-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  column-gap: 16px;
}
.advanced-settings {
  margin-bottom: 12px;
}
.advanced-settings :deep(.ant-collapse-content-box) {
  border-top: 1px solid var(--gray-150);
  background: var(--bg-container);
  padding: 16px;
}
.advanced-settings :deep(.ant-collapse) { border-color: var(--gray-150); background: var(--gray-10); }
.advanced-settings :deep(.ant-collapse-header) { color: var(--color-text) !important; }
.advanced-settings :deep(.ant-checkbox-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.step-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  position: sticky;
  bottom: 0;
  z-index: 1;
  background: var(--bg-container);
  border-top: 1px solid var(--gray-150);
  padding: 12px 0;
}
.result-section {
  max-width: 620px;
  margin: 0 auto;
}
.retained-resources {
  border-top: 1px solid var(--gray-150);
}
.retained-row {
  margin: 0;
  border-top: 0;
}
.retained-row span:first-child {
  width: 142px;
  flex: 0 0 142px;
  color: var(--color-text-tertiary);
}
@media (max-width: 640px) {
  .workflow-steps :deep(.ant-steps-item-title) {
    font-size: 11px;
  }
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .credential-choice {
    align-items: stretch;
    flex-direction: column;
  }
  .catalog-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .step-actions {
    flex-wrap: wrap;
  }
  .step-actions :deep(.ant-btn) {
    flex: 1 1 100%;
    margin-left: 0;
  }
  .resource-strip {
    align-items: flex-start;
    flex-direction: column;
  }
  .strip-alias {
    margin-left: 0;
  }
  .retained-row span:first-child {
    width: 112px;
    flex-basis: 112px;
  }
}
</style>
