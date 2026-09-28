<template>
  <a-drawer
    v-model:open="visible" title="接入模型" width="min(840px, 100vw)" destroy-on-close
    :closable="!submitting" :mask-closable="!submitting" :keyboard="!submitting"
    root-class-name="model-onboarding-drawer" @close="invalidateRequests"
  >
    <a-spin :spinning="bootLoading">
      <a-alert v-if="bootError" :message="bootError" type="error" show-icon>
        <template #description><a-button type="link" @click="loadOptions">重新加载</a-button></template>
      </a-alert>
      <a-result v-else-if="published" status="success" title="接入成功" :sub-title="resources.model?.displayName">
        <template #extra><a-button type="primary" @click="visible = false">完成</a-button></template>
      </a-result>
      <div v-else class="onboarding-form">
        <section class="form-section">
          <div class="section-heading"><h3>模型信息</h3><p>先确定模型用途，再填写服务商提供的模型名称。</p></div>
          <a-form ref="modelFormRef" :model="form" :rules="rules" layout="vertical">
            <a-form-item label="模型类型" name="modelType">
              <a-radio-group v-model:value="form.modelType" :disabled="inputsDisabled" class="model-type-options" button-style="solid">
                <a-radio-button value="chat">对话</a-radio-button>
                <a-radio-button value="embedding">向量</a-radio-button>
                <a-radio-button value="rerank">重排序</a-radio-button>
              </a-radio-group>
              <div class="field-description">{{ typeDescription }}</div>
            </a-form-item>
            <div class="form-grid">
              <a-form-item label="模型名称" name="upstreamModel">
                <a-input v-model:value="form.upstreamModel" :disabled="inputsDisabled" :maxlength="200" :placeholder="modelPlaceholder" />
              </a-form-item>
              <a-form-item label="显示名称" name="displayName">
                <a-input v-model:value="form.displayName" :disabled="inputsDisabled" :maxlength="100" :placeholder="form.upstreamModel ? identity.displayName : '留空时使用模型名称'" />
              </a-form-item>
            </div>
          </a-form>
        </section>

        <section class="form-section">
          <div class="section-heading"><h3>连接配置</h3><p>选择服务商，复用已保存的连接，或填写新的访问凭据。</p></div>
          <a-form layout="vertical">
            <div class="form-grid">
              <a-form-item label="服务商">
                <a-select v-model:value="selectedProvider" :disabled="inputsDisabled" show-search option-filter-prop="label" :options="providerOptions" placeholder="选择服务商" />
              </a-form-item>
              <a-form-item label="连接方式">
                <a-segmented v-model:value="credentialMode" :disabled="inputsDisabled" :options="credentialModes" />
              </a-form-item>
            </div>
            <a-form-item v-if="credentialMode === 'existing'" label="已保存连接">
              <a-select v-model:value="selectedCredential" :disabled="inputsDisabled" :options="credentialOptions" show-search option-filter-prop="label" placeholder="选择已保存的连接" />
              <div v-if="selectedConnection" class="field-description connection-endpoint">服务地址：{{ selectedConnection.connectionProfile.apiBase || '使用服务商默认地址' }}</div>
            </a-form-item>
          </a-form>
          <CredentialForm v-if="credentialMode === 'new'" ref="credentialFormRef" quick embedded
            :providers="providers" :credential-name="autoCredentialName" :provider="selectedProvider"
            :disabled="inputsDisabled" @change="invalidateTest" />
        </section>

        <a-collapse :bordered="false" class="advanced-settings">
          <a-collapse-panel key="advanced" header="高级设置">
            <a-form layout="vertical">
              <div class="form-grid">
                <a-form-item label="请求超时（秒）"><a-input-number v-model:value="form.timeout" :disabled="inputsDisabled" :min="0.1" :max="600" placeholder="使用默认值" /></a-form-item>
                <a-form-item label="最大重试次数"><a-input-number v-model:value="form.maxRetries" :disabled="inputsDisabled" :min="0" :max="10" :precision="0" placeholder="使用默认值" /></a-form-item>
                <a-form-item v-if="form.modelType === 'embedding'" label="默认向量维度"><a-input-number v-model:value="form.dimensions" :disabled="inputsDisabled" :min="1" :max="65536" :precision="0" placeholder="跟随模型默认值" /></a-form-item>
                <a-form-item v-if="form.modelType === 'rerank'" label="默认 Top N"><a-input-number v-model:value="form.topN" :disabled="inputsDisabled" :min="1" :max="256" :precision="0" placeholder="可选" /></a-form-item>
                <a-form-item v-if="form.modelType === 'rerank'" label="返回排序后的文档"><a-switch v-model:checked="form.returnDocuments" :disabled="inputsDisabled" /></a-form-item>
              </div>
            </a-form>
          </a-collapse-panel>
        </a-collapse>
        <a-alert v-if="failureMessage" :message="failureMessage" type="error" show-icon class="status-message" />
        <a-alert v-else-if="connectionTested" :message="`连接测试通过${latencyMs == null ? '' : ` · ${latencyMs} ms`}`" type="success" show-icon class="status-message" />
        <p v-if="locked && !submitting" class="field-description">当前配置已锁定，可直接重试接入；完成后可在模型目录中管理。</p>
      </div>
    </a-spin>
    <template v-if="!published && !bootError" #footer>
      <div class="onboarding-footer">
        <span class="footer-status" role="status">{{ progress || '测试会向服务商发送一次最小请求。' }}</span>
        <div class="footer-actions">
          <a-button :disabled="submitting" @click="visible = false">取消</a-button>
          <a-button :loading="testLoading" :disabled="bootLoading || submitting" @click="testConnection"><ApiOutlined />测试连接</a-button>
          <a-button type="primary" :loading="submitting" :disabled="bootLoading || testLoading || !connectionTested" @click="publish"><CheckOutlined />{{ failureMessage && locked ? '重试接入' : '接入模型' }}</a-button>
        </div>
      </div>
    </template>
  </a-drawer>
</template>

<script setup>
import { ApiOutlined, CheckOutlined } from '@ant-design/icons-vue'
import {
  createEngineCredential, createEngineDeployment, listEngineCredentials, listEngineDeployments,
  listEngineProviders, testEngineConnection,
} from '../../../api/modelEngine.js'
import { createPlatformModel, getPlatformModel, listPlatformModels, publishPlatformModel, savePlatformModelBinding } from '../../../api/modelCatalog.js'
import {
  buildQuickResourceIdentity, createRequestGate, curateModelProviders, defaultModelCapabilities,
  isCredentialCompatible, isManagementMisconfiguredError, normalizeCredential, normalizeDeployment,
  normalizeProvider, reconcileDeployment, shouldUpdateBinding,
} from '../../../utils/modelEngine.js'
import CredentialForm from './CredentialForm.vue'
import { connectionFailureMessage } from '../../../utils/connectionDiagnostics.js'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'completed'])
const visible = computed({ get: () => props.modelValue, set: (value) => emit('update:modelValue', value) })
const modelFormRef = ref()
const credentialFormRef = ref()
const bootLoading = ref(false)
const bootError = ref('')
const submitting = ref(false)
const testLoading = ref(false)
const published = ref(false)
const saveStarted = ref(false)
const attempted = { credential: false, model: false }
const failureMessage = ref('')
const progress = ref('')
const connectionTested = ref(false)
let configurationRevision = 0
const latencyMs = ref(null)
const providers = ref([])
const credentials = ref([])
const selectedProvider = ref('')
const credentialMode = ref('new')
const selectedCredential = ref('')
const resourceSuffix = ref('')
const resources = reactive({ credential: null, deployment: null, model: null, binding: null })
const requestGate = createRequestGate()
const emptyForm = () => ({ modelType: 'chat', upstreamModel: '', displayName: '', timeout: null, maxRetries: null, dimensions: null, topN: null, returnDocuments: false })
const form = reactive(emptyForm())
const rules = {
  modelType: [{ required: true, message: '请选择模型类型' }],
  upstreamModel: [{ required: true, whitespace: true, message: '请输入服务商提供的模型名称', trigger: 'blur' }],
  displayName: [{ max: 100, message: '显示名称不能超过 100 个字符', trigger: 'blur' }],
}
const locked = computed(() => saveStarted.value)
const inputsDisabled = computed(() => bootLoading.value || testLoading.value || submitting.value || locked.value)
const identity = computed(() => buildQuickResourceIdentity({ provider: selectedProvider.value, upstreamModel: form.upstreamModel.trim(), suffix: resourceSuffix.value }))
const autoCredentialName = computed(() => identity.value.credentialName)
const providerOptions = computed(() => providers.value.map((item) => ({ label: item.displayName, value: item.provider })))
const compatibleCredentials = computed(() => credentials.value.filter((item) => isCredentialCompatible(item, selectedProvider.value, providers.value, { requireProfile: true })))
const selectedConnection = computed(() => compatibleCredentials.value.find((item) => item.name === selectedCredential.value))
const credentialOptions = computed(() => compatibleCredentials.value.map((item) => ({ label: item.name, value: item.name })))
const credentialModes = computed(() => [
  { label: '使用已有连接', value: 'existing', disabled: !compatibleCredentials.value.length },
  { label: '新建连接', value: 'new' },
])
const typeDescription = computed(() => ({ chat: '用于聊天、内容生成和智能体调用。', embedding: '将文本转换为向量，用于知识库索引与检索。', rerank: '对检索结果重新排序，提高相关性。' })[form.modelType])
const modelPlaceholder = computed(() => {
  if (selectedProvider.value === 'OpenAI_Compatible') return { chat: '例如 deepseek-ai/DeepSeek-V4-Pro', embedding: '例如 Qwen/Qwen3-Embedding-8B', rerank: '例如 BAAI/bge-reranker-v2-m3' }[form.modelType]
  if (selectedProvider.value === 'Deepseek' && form.modelType === 'chat') return '例如 deepseek-v4-pro'
  return { chat: '填写当前服务商的完整模型名称', embedding: '填写向量模型的完整名称', rerank: '填写重排序模型的完整名称' }[form.modelType]
})

function invalidateTest() {
  configurationRevision += 1
  if (!locked.value && !submitting.value) resources.credential = null
  connectionTested.value = false
  latencyMs.value = null
  failureMessage.value = ''
}
watch(() => [form.modelType, form.upstreamModel, form.timeout, form.maxRetries, form.dimensions, form.topN, form.returnDocuments, selectedCredential.value, credentialMode.value], invalidateTest, { flush: 'sync' })
watch(selectedProvider, () => {
  selectedCredential.value = compatibleCredentials.value[0]?.name || ''
  credentialMode.value = selectedCredential.value ? 'existing' : 'new'
  invalidateTest()
}, { flush: 'sync' })
watch(() => form.modelType, () => { form.dimensions = null; form.topN = null; form.returnDocuments = false }, { flush: 'sync' })

function resetWorkflow() {
  saveStarted.value = false
  attempted.credential = false; attempted.model = false
  Object.assign(form, emptyForm())
  Object.assign(resources, { credential: null, deployment: null, model: null, binding: null })
  providers.value = []; credentials.value = []; selectedProvider.value = ''; selectedCredential.value = ''
  credentialMode.value = 'new'
  const bytes = new Uint32Array(2)
  resourceSuffix.value = globalThis.crypto?.getRandomValues
    ? Array.from(globalThis.crypto.getRandomValues(bytes), (value) => value.toString(16).padStart(8, '0')).join('')
    : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`.slice(0, 16)
  bootError.value = ''; failureMessage.value = ''; progress.value = ''
  connectionTested.value = false; latencyMs.value = null; published.value = false; submitting.value = false; testLoading.value = false
}

async function loadOptions() {
  const generation = requestGate.begin()
  bootLoading.value = true; bootError.value = ''
  try {
    const [providerResponse, credentialResponse] = await Promise.all([listEngineProviders(), listEngineCredentials()])
    if (!requestGate.isCurrent(generation)) return
    providers.value = curateModelProviders((providerResponse?.data || []).map(normalizeProvider).filter(Boolean))
    credentials.value = (credentialResponse?.data || []).map(normalizeCredential)
    selectedProvider.value = providers.value[0]?.provider || ''
    if (!providers.value.length) bootError.value = '暂无可用服务商，请检查模型服务配置'
  } catch (error) {
    if (requestGate.isCurrent(generation)) bootError.value = isManagementMisconfiguredError(error) ? '模型管理服务尚未配置' : '连接配置加载失败'
  } finally { if (requestGate.isCurrent(generation)) bootLoading.value = false }
}

async function readConfiguration() {
  const valid = await modelFormRef.value?.validateFields().then(() => true).catch(() => false)
  if (!valid) return null
  if (!selectedProvider.value) { failureMessage.value = '请选择服务商'; return null }
  let credentialPayload = null
  let credentialName = resources.credential?.name || ''
  if (!credentialName && credentialMode.value === 'existing') {
    credentialName = compatibleCredentials.value.find((item) => item.name === selectedCredential.value)?.name || ''
    if (!credentialName) { failureMessage.value = '请选择已保存的连接'; return null }
  } else if (!credentialName) {
    credentialPayload = await credentialFormRef.value?.getPayload()
    if (!credentialPayload) return null
  }
  const defaults = form.modelType === 'embedding'
    ? (form.dimensions == null ? {} : { dimensions: form.dimensions })
    : (form.modelType === 'rerank' ? { ...(form.topN == null ? {} : { topN: form.topN }), returnDocuments: form.returnDocuments } : {})
  return {
    provider: selectedProvider.value, upstreamModel: form.upstreamModel.trim(), modelType: form.modelType,
    displayName: form.displayName.trim() || identity.value.displayName,
    modelCode: `${identity.value.modelCode.slice(0, 80)}-${resourceSuffix.value}`,
    alias: identity.value.deploymentAlias, credentialPayload, credentialName, defaults,
    deploymentOptions: { ...(form.timeout == null ? {} : { timeout: form.timeout }), ...(form.maxRetries == null ? {} : { maxRetries: form.maxRetries }) },
  }
}

function testPayload(config, credentialName = config.credentialName) {
  return { modelType: config.modelType, upstreamModel: config.upstreamModel, provider: config.provider,
    ...(credentialName ? { credentialName, values: {} } : { values: config.credentialPayload.values }) }
}

async function testConnection() {
  if (testLoading.value || submitting.value) return
  const generation = requestGate.begin()
  testLoading.value = true; failureMessage.value = ''; connectionTested.value = false
  try {
    const config = await readConfiguration()
    if (!config || !requestGate.isCurrent(generation)) return
    const revision = configurationRevision
    const response = await testEngineConnection(testPayload(config))
    if (!requestGate.isCurrent(generation)) return
    if (revision !== configurationRevision) {
      failureMessage.value = '连接配置已变化，请重新测试'
      return
    }
    if (!response?.data?.success) {
      failureMessage.value = connectionFailureMessage(response?.data)
      return
    }
    latencyMs.value = response.data.latencyMs ?? null
    connectionTested.value = true
  } catch (error) {
    if (requestGate.isCurrent(generation)) failureMessage.value = connectionFailureMessage(null, error)
  } finally { if (requestGate.isCurrent(generation)) testLoading.value = false }
}

class OnboardingError extends Error {}

function checkCurrent(generation) {
  if (!requestGate.isCurrent(generation)) throw new Error('closed')
}

async function findSavedCredential(config, generation) {
  const response = await listEngineCredentials()
  checkCurrent(generation)
  const found = (response?.data || []).map(normalizeCredential).find((item) => item.name === config.credentialPayload.credentialName)
  if (!found) return false
  if (!isCredentialCompatible(found, config.provider, providers.value, { requireProfile: true })) throw new OnboardingError('已保存连接的服务地址与当前服务商不一致，请检查连接配置')
  const tested = await testEngineConnection(testPayload(config, found.name))
  checkCurrent(generation)
  if (!tested?.data?.success) throw new OnboardingError(connectionFailureMessage(tested?.data))
  resources.credential = { name: found.name, created: true }
  return true
}

async function ensureCredential(config, generation) {
  if (resources.credential) return
  if (config.credentialName) { resources.credential = { name: config.credentialName, created: false }; return }
  // 上次写请求结果未知时先核对；查询失败不会再次写入。
  if (attempted.credential && await findSavedCredential(config, generation)) return
  attempted.credential = true
  try {
    await createEngineCredential(config.credentialPayload)
    checkCurrent(generation)
    resources.credential = { name: config.credentialPayload.credentialName, created: true }
  } catch {
    checkCurrent(generation)
    if (!await findSavedCredential(config, generation)) throw new OnboardingError('连接保存失败，当前输入已保留，请重试')
  }
}

async function lookupDeployment(payload) {
  const response = await listEngineDeployments({ modelAlias: payload.modelAlias })
  return reconcileDeployment((response?.data || []).map(normalizeDeployment), payload, providers.value)
}

async function ensureDeployment(config, generation) {
  if (resources.deployment) return
  const payload = { modelAlias: config.alias, upstreamModel: config.upstreamModel, provider: config.provider,
    credentialName: resources.credential.name, ...config.deploymentOptions }
  const before = await lookupDeployment(payload)
  checkCurrent(generation)
  if (before.kind === 'match') { resources.deployment = before.deployment; return }
  if (before.kind === 'conflict') throw new OnboardingError('自动生成的模型配置发生冲突，请重新打开接入表单')
  try {
    const response = await createEngineDeployment(payload)
    checkCurrent(generation)
    const created = normalizeDeployment(response?.data)
    if (created.deploymentId && reconcileDeployment([created], payload, providers.value).kind === 'match') {
      resources.deployment = created; return
    }
  } catch { checkCurrent(generation) }
  const after = await lookupDeployment(payload)
  checkCurrent(generation)
  if (after.kind !== 'match') throw new OnboardingError('模型配置尚未确认，请重试接入；已保存的连接会被复用')
  resources.deployment = after.deployment
}

async function findSavedModel(config, generation) {
  const response = await listPlatformModels({ modelCode: config.modelCode, pageNum: 1, pageSize: 100 })
  checkCurrent(generation)
  const found = (response?.rows || []).find((item) => item.modelCode === config.modelCode)
  if (!found) return false
  if (found.modelType !== config.modelType || found.displayName !== config.displayName || found.lifecycleStatus !== 'draft') {
    throw new OnboardingError('模型信息发生冲突，请重新打开接入表单')
  }
  const detail = await getPlatformModel(found.modelId)
  checkCurrent(generation)
  if (detail?.data?.binding && detail.data.binding.engineResourceId !== config.alias) throw new OnboardingError('模型信息发生冲突，请重新打开接入表单')
  resources.model = detail.data
  resources.binding = detail.data.binding || null
  return true
}

async function ensureCatalog(config, generation) {
  if (resources.model) return
  if (attempted.model && await findSavedModel(config, generation)) return
  attempted.model = true
  try {
    const response = await createPlatformModel({ modelCode: config.modelCode, displayName: config.displayName,
      modelType: config.modelType, capabilities: defaultModelCapabilities(config.modelType), defaultParameters: config.defaults })
    checkCurrent(generation)
    if (!response?.data?.modelId) throw new Error('missing model')
    resources.model = response.data
  } catch {
    checkCurrent(generation)
    if (!await findSavedModel(config, generation)) throw new OnboardingError('模型信息保存失败，当前配置已保留，请重试接入')
  }
}

async function publish() {
  if (submitting.value || testLoading.value || !connectionTested.value) return
  const generation = requestGate.begin()
  submitting.value = true; failureMessage.value = ''
  try {
    const config = await readConfiguration()
    if (!config || !requestGate.isCurrent(generation) || !connectionTested.value) return
    saveStarted.value = true
    progress.value = '正在保存连接…'
    await ensureCredential(config, generation)
    progress.value = '正在配置模型…'
    await ensureDeployment(config, generation)
    await ensureCatalog(config, generation)
    if (!resources.binding) {
      const detail = await getPlatformModel(resources.model.modelId)
      checkCurrent(generation)
      resources.binding = detail?.data?.binding || null
    }
    if (shouldUpdateBinding(resources.binding, resources.deployment.alias)) {
      const response = await savePlatformModelBinding(resources.model.modelId, {
        engineResourceId: resources.deployment.alias, bindingStatus: 'active', engineMetadata: {},
        ...(resources.binding?.version ? { expectedVersion: resources.binding.version } : {}),
      })
      checkCurrent(generation); resources.binding = response?.data
    }
    progress.value = '正在启用模型…'
    const response = await publishPlatformModel(resources.model.modelId)
    checkCurrent(generation)
    resources.model = response?.data || resources.model
    published.value = true
    credentialFormRef.value?.clearSecrets()
    emit('completed')
  } catch (error) {
    if (requestGate.isCurrent(generation)) failureMessage.value = error instanceof OnboardingError
      ? error.message : '接入未完成，已保存的配置会被复用，请重试接入'
  } finally { if (requestGate.isCurrent(generation)) { submitting.value = false; progress.value = '' } }
}

function invalidateRequests() { requestGate.invalidate(); credentialFormRef.value?.clearSecrets() }
watch(() => props.modelValue, (open) => {
  if (!open) { invalidateRequests(); return }
  requestGate.activate(); resetWorkflow(); loadOptions()
})
onBeforeUnmount(invalidateRequests)
</script>

<style scoped>
.onboarding-form { color: var(--color-text); font-size: 14px; }
.form-section + .form-section { border-top: 1px solid var(--gray-150); margin-top: 8px; padding-top: 24px; }
.section-heading { margin-bottom: 20px; }
.section-heading h3 { margin: 0; color: var(--color-text); font-size: 15px; font-weight: 600; }
.section-heading p { margin: 6px 0 0; color: var(--color-text-secondary); font-size: 13px; line-height: 1.6; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; }
.model-type-options { display: flex; }
.model-type-options :deep(.ant-radio-button-wrapper) { flex: 1; text-align: center; }
.field-description { margin-top: 8px; color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.connection-endpoint { overflow-wrap: anywhere; }
.onboarding-form :deep(.ant-input-number) { width: 100%; }
.onboarding-form :deep(.engine-form) { padding: 0; }
.onboarding-form :deep(.ant-form-item-label > label) { color: var(--color-text); }
.advanced-settings { margin: 8px 0 16px; border: 1px solid var(--gray-150); background: transparent; border-radius: 6px; }
.advanced-settings :deep(.ant-collapse-header) { color: var(--color-text) !important; }
.advanced-settings :deep(.ant-collapse-content-box) { padding: 16px 20px 0; background: var(--bg-container); }
.status-message { margin-top: 16px; }
.onboarding-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 6px 0; }
.footer-status { color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.footer-actions { display: flex; flex-shrink: 0; gap: 8px; }
</style>
