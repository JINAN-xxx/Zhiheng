<template>
  <el-drawer
    v-model="visible"
    title="接入模型"
    size="min(760px, 100%)"
    destroy-on-close
    class="model-onboarding-drawer"
    @close="invalidateRequests"
  >
    <div v-loading="bootLoading" class="onboarding-body">
      <el-alert v-if="bootError" :title="bootError" type="error" :closable="false" show-icon>
        <template #default><el-button link type="primary" @click="loadOptions">重新加载</el-button></template>
      </el-alert>

      <template v-else>
        <el-steps :active="activeStep" finish-status="success" align-center class="workflow-steps">
          <el-step title="Provider 凭据" />
          <el-step title="引擎部署" />
          <el-step title="平台模型" />
          <el-step title="发布" />
        </el-steps>

        <el-alert v-if="failureMessage" title="接入未完成" type="error" :closable="false" show-icon class="workflow-error">
          <template #default>
            <div class="workflow-error-content">
              <span>{{ failureMessage }}</span>
              <div>
                <el-button type="primary" link :icon="RefreshRight" @click="retry">重试当前步骤</el-button>
                <el-button link @click="visible = false">关闭</el-button>
              </div>
            </div>
          </template>
        </el-alert>

        <section v-if="workflowState === 'credential'" class="step-section">
          <div class="section-heading">
            <h3>Provider 凭据</h3>
            <el-segmented v-model="credentialMode" :options="credentialModes" />
          </div>
          <template v-if="credentialMode === 'existing'">
            <el-form label-position="top">
              <el-form-item label="选择凭据">
                <el-select v-model="selectedCredential" filterable placeholder="选择现有凭据" style="width: 100%">
                  <el-option
                    v-for="item in credentials"
                    :key="item.name"
                    :label="`${item.name} · ${item.provider}`"
                    :value="item.name"
                  />
                </el-select>
              </el-form-item>
            </el-form>
            <div class="step-actions">
              <el-button type="primary" :disabled="!selectedCredential" :icon="ArrowRight" @click="useCredential">下一步</el-button>
            </div>
          </template>
          <CredentialForm
            v-else
            :providers="providers"
            :submitting="submitting"
            @submit="createCredential"
            @cancel="visible = false"
          />
        </section>

        <section v-if="workflowState === 'deployment'" class="step-section">
          <div class="resource-strip">
            <span>当前凭据</span><code>{{ resources.credential?.name }}</code>
            <el-tag size="small" type="success" effect="plain">已保留</el-tag>
          </div>
          <DeploymentForm
            :providers="providers"
            :credentials="credentials"
            :initial-credential-name="resources.credential?.name || ''"
            :initial-provider="resources.credential?.provider || ''"
            :submitting="submitting"
            :test-loading="testLoading"
            :tested="connectionTested"
            require-test
            @change="connectionTested = false"
            @test="testConnection"
            @submit="createDeployment"
            @cancel="visible = false"
          />
        </section>

        <section v-if="workflowState === 'catalog'" class="step-section">
          <div class="resource-strip">
            <span>Deployment</span><code>{{ resources.deployment?.deploymentId }}</code>
            <span class="strip-alias">Alias: <code>{{ resources.deployment?.alias }}</code></span>
          </div>
          <div class="section-heading">
            <h3>平台模型</h3>
            <el-segmented v-model="catalogMode" :options="catalogModes" :disabled="Boolean(resources.model)" />
          </div>
          <el-form ref="catalogFormRef" :model="catalogForm" :rules="catalogRules" label-position="top">
            <el-form-item v-if="catalogMode === 'existing'" label="选择草稿模型" prop="existingModelId">
              <el-select v-model="catalogForm.existingModelId" filterable placeholder="选择平台模型草稿" style="width: 100%" :disabled="Boolean(resources.model)">
                <el-option v-for="item in draftModels" :key="item.modelId" :label="`${item.modelCode} · ${item.displayName}`" :value="item.modelId" />
              </el-select>
            </el-form-item>
            <div v-else class="catalog-grid">
              <el-form-item label="Model Code" prop="modelCode">
                <el-input v-model="catalogForm.modelCode" placeholder="例如 chat-pro" :disabled="Boolean(resources.model)" />
              </el-form-item>
              <el-form-item label="显示名称" prop="displayName">
                <el-input v-model="catalogForm.displayName" placeholder="例如 Chat Pro" :disabled="Boolean(resources.model)" />
              </el-form-item>
              <el-form-item label="模型类型" prop="modelType">
                <el-segmented v-model="catalogForm.modelType" :options="modelTypes" :disabled="Boolean(resources.model)" />
              </el-form-item>
              <el-form-item label="Capability">
                <el-checkbox-group v-model="catalogForm.capabilities" :disabled="Boolean(resources.model)">
                  <el-checkbox-button value="streaming">流式</el-checkbox-button>
                  <el-checkbox-button value="reasoning">推理</el-checkbox-button>
                  <el-checkbox-button value="vision">视觉</el-checkbox-button>
                  <el-checkbox-button value="tools">工具</el-checkbox-button>
                </el-checkbox-group>
              </el-form-item>
            </div>
            <div class="step-actions">
              <el-button type="primary" :loading="submitting" :icon="Promotion" @click="publish">绑定并发布</el-button>
            </div>
          </el-form>
        </section>

        <section v-if="workflowState === 'published'" class="result-section">
          <el-result icon="success" title="模型已发布" :sub-title="resources.model?.modelCode || ''">
            <template #extra><el-button type="primary" :icon="Check" @click="visible = false">完成</el-button></template>
          </el-result>
          <div class="retained-resources">
            <div class="retained-row"><span>Platform Model</span><code>{{ resources.model?.modelId }}</code></div>
            <div class="retained-row"><span>Binding Alias</span><code>{{ resources.deployment?.alias }}</code></div>
          </div>
        </section>
      </template>
    </div>
  </el-drawer>
</template>

<script setup>
import { ArrowRight, Check, Promotion, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createEngineCredential, createEngineDeployment, listEngineCredentials, listEngineDeployments, listEngineProviders, testEngineConnection,
} from '../../../api/modelEngine.js'
import {
  createPlatformModel, getPlatformModel, listPlatformModels, publishPlatformModel, savePlatformModelBinding,
} from '../../../api/modelCatalog.js'
import {
  collectAllPages,
  createRequestGate,
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
const visible = computed({ get: () => props.modelValue, set: (value) => emit('update:modelValue', value) })

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
const credentialMode = ref('existing')
const selectedCredential = ref('')
const catalogMode = ref('new')
const catalogFormRef = ref()
const resources = reactive({ credential: null, deployment: null, model: null, binding: null })
const requestGate = createRequestGate()
const catalogForm = reactive({ existingModelId: '', modelCode: '', displayName: '', modelType: 'chat', capabilities: ['streaming'] })

const credentialModes = [{ label: '使用现有', value: 'existing' }, { label: '新建凭据', value: 'new' }]
const catalogModes = [{ label: '新建模型', value: 'new' }, { label: '使用草稿', value: 'existing' }]
const modelTypes = [{ label: 'Chat', value: 'chat' }, { label: 'Embedding', value: 'embedding' }]
const catalogRules = {
  existingModelId: [{ required: true, message: '请选择平台模型草稿', trigger: 'change' }],
  modelCode: [
    { required: true, message: 'Model Code 不能为空', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/, message: 'Model Code 格式不正确', trigger: 'blur' },
  ],
  displayName: [{ required: true, message: '显示名称不能为空', trigger: 'blur' }],
  modelType: [{ required: true, message: '请选择模型类型', trigger: 'change' }],
}

const activeStep = computed(() => {
  return { credential: 0, deployment: 1, catalog: 2, published: 4 }[workflowState.value] ?? 0
})
const draftModels = computed(() => models.value.filter((item) => item.lifecycleStatus === 'draft'))

function resetWorkflow() {
  workflowState.value = 'credential'; failedStep.value = 'credential'; failureMessage.value = ''
  bootLoading.value = false; submitting.value = false; testLoading.value = false
  credentialMode.value = 'existing'; selectedCredential.value = ''; catalogMode.value = 'new'
  connectionTested.value = false
  resources.credential = null; resources.deployment = null; resources.model = null; resources.binding = null
  Object.assign(catalogForm, { existingModelId: '', modelCode: '', displayName: '', modelType: 'chat', capabilities: ['streaming'] })
}

async function loadOptions() {
  const requestGeneration = requestGate.begin()
  bootLoading.value = true; bootError.value = ''
  try {
    const [providerResponse, credentialResponse, modelRows] = await Promise.all([
      listEngineProviders(),
      listEngineCredentials(),
      collectAllPages(({ pageNum, pageSize }) => listPlatformModels({ pageNum, pageSize }), { pageSize: 100 }),
    ])
    if (!requestGate.isCurrent(requestGeneration)) return
    providers.value = (providerResponse?.data || []).map(normalizeProvider).filter(Boolean)
    credentials.value = (credentialResponse?.data || []).map(normalizeCredential)
    models.value = modelRows.map(normalizePlatformModel)
    if (credentials.value.length === 0) credentialMode.value = 'new'
  } catch (event) {
    if (requestGate.isCurrent(requestGeneration)) bootError.value = isManagementMisconfiguredError(event) ? 'Management Key 未配置' : '接入资源加载失败'
  }
  finally { if (requestGate.isCurrent(requestGeneration)) bootLoading.value = false }
}

function fail(step, message) { failedStep.value = step; failureMessage.value = message }

function useCredential() {
  const credential = credentials.value.find((item) => item.name === selectedCredential.value)
  if (!credential) return
  resources.credential = credential
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
      deploymentCount: 0,
      createdAt: null,
      updatedAt: null,
    }
    credentials.value.push(resources.credential)
    workflowState.value = 'deployment'
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('credential', 'Provider 凭据创建失败，当前输入已保留')
  }
  finally { if (requestGate.isCurrent(requestGeneration)) submitting.value = false }
}

async function testConnection(payload) {
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  try {
    await ElMessageBox.confirm('连接测试可能触发 Provider 请求并产生极小费用，确认继续？', '测试连接', { type: 'warning' })
  } catch { return }
  if (!requestGate.isCurrent(requestGeneration)) return
  testLoading.value = true
  try {
    const response = await testEngineConnection(payload)
    if (!requestGate.isCurrent(requestGeneration)) return
    if (!response?.data?.success) throw new Error('connection rejected')
    connectionTested.value = true
    ElMessage.success(`连接成功${response.data.latencyMs != null ? `，${response.data.latencyMs} ms` : ''}`)
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('deployment', '连接测试失败，当前部署输入已保留')
  }
  finally { if (requestGate.isCurrent(requestGeneration)) testLoading.value = false }
}

async function lookupDeployment(payload) {
  const response = await listEngineDeployments()
  const deployments = (response?.data || []).map(normalizeDeployment)
  return reconcileDeployment(deployments, payload, providers.value)
}

function reuseDeployment(deployment) {
  resources.deployment = deployment
  failureMessage.value = ''
  workflowState.value = 'catalog'
}

async function createDeployment(payload) {
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  submitting.value = true
  let beforeCreate
  try {
    beforeCreate = await lookupDeployment(payload)
  } catch {
    if (requestGate.isCurrent(requestGeneration)) fail('deployment', '创建前核对部署失败，未发起创建；当前输入已保留')
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
    fail('deployment', '同名部署已存在但配置不一致，请修改路由别名或对齐全部部署参数')
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
      fail('deployment', '部署创建响应与提交配置不一致，已停止继续；当前输入已保留，系统未执行自动删除')
    }
  } catch {
    if (!requestGate.isCurrent(requestGeneration)) return
    try {
      const afterCreate = await lookupDeployment(payload)
      if (!requestGate.isCurrent(requestGeneration)) return
      if (afterCreate.kind === 'match') {
        reuseDeployment(afterCreate.deployment)
        ElMessage.warning('创建响应结果未知，已核对部署实际存在并继续')
      } else if (afterCreate.kind === 'conflict') {
        fail('deployment', '创建结果未知；已核对到同名但配置不一致的部署，请检查后重试')
      } else {
        fail('deployment', '部署创建失败；已核对未发现匹配部署，可重试当前步骤且不会自动删除已创建资源')
      }
    } catch {
      if (requestGate.isCurrent(requestGeneration)) fail('deployment', '创建结果未知且核对失败；重试时将先重新核对，不会盲目重复创建')
    }
  } finally {
    if (requestGate.isCurrent(requestGeneration)) submitting.value = false
  }
}

function capabilityPayload() {
  const selected = new Set(catalogForm.capabilities)
  return { vision: selected.has('vision'), reasoning: selected.has('reasoning'), tools: selected.has('tools'), streaming: selected.has('streaming') }
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
    capabilities: capabilityPayload(),
    defaultParameters: {},
  })
  if (!requestGate.isCurrent(requestGeneration)) return null
  resources.model = response?.data
  models.value.push(normalizePlatformModel(resources.model))
  return resources.model
}

async function publish() {
  const fields = catalogMode.value === 'existing' ? ['existingModelId'] : ['modelCode', 'displayName', 'modelType']
  const valid = await catalogFormRef.value.validateField(fields).then(() => true).catch(() => false)
  if (!valid) return
  const requestGeneration = requestGate.begin()
  failureMessage.value = ''
  submitting.value = true
  try {
    const model = await ensureCatalogModel(requestGeneration)
    if (!requestGate.isCurrent(requestGeneration) || !model) return
    if (shouldUpdateBinding(resources.binding, resources.deployment.alias)) {
      const bindingPayload = { engineResourceId: resources.deployment.alias, bindingStatus: 'active', engineMetadata: {} }
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
    if (requestGate.isCurrent(requestGeneration)) fail('catalog', '平台模型绑定或发布失败，当前输入已保留')
  }
  finally { if (requestGate.isCurrent(requestGeneration)) submitting.value = false }
}

function retry() { workflowState.value = failedStep.value; failureMessage.value = '' }
function invalidateRequests() { requestGate.invalidate() }

watch(() => props.modelValue, (open) => {
  if (!open) {
    invalidateRequests()
    return
  }
  requestGate.activate()
  resetWorkflow()
  loadOptions()
})
onBeforeUnmount(invalidateRequests)
</script>

<style scoped>
.onboarding-body { min-height: 460px; font-size: 13px; }
.workflow-steps { margin: 4px 0 28px; }
.workflow-error { margin-bottom: 18px; }
.workflow-error-content { display: flex; min-width: 0; flex-direction: column; gap: 4px; }
.step-section { min-width: 0; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.section-heading h3 { margin: 0; font-size: 16px; font-weight: 600; letter-spacing: 0; }
.resource-strip, .retained-row { display: flex; min-width: 0; align-items: center; gap: 10px; border-top: 1px solid var(--el-border-color-lighter); border-bottom: 1px solid var(--el-border-color-lighter); padding: 10px 0; margin-bottom: 18px; }
.resource-strip code, .retained-row code { min-width: 0; overflow: hidden; font-family: var(--ai-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.strip-alias { margin-left: auto; color: var(--el-text-color-secondary); }
.catalog-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 16px; }
.step-actions { display: flex; justify-content: flex-end; padding-top: 12px; }
.result-section { max-width: 620px; margin: 0 auto; }
.retained-resources { border-top: 1px solid var(--el-border-color-lighter); }
.retained-row { margin: 0; border-top: 0; }
.retained-row span:first-child { width: 142px; flex: 0 0 142px; color: var(--el-text-color-secondary); }
@media (max-width: 640px) {
  .workflow-steps :deep(.el-step__title) { font-size: 11px; }
  .section-heading { align-items: flex-start; flex-direction: column; }
  .catalog-grid { grid-template-columns: minmax(0, 1fr); }
  .resource-strip { align-items: flex-start; flex-direction: column; }
  .strip-alias { margin-left: 0; }
  .retained-row span:first-child { width: 112px; flex-basis: 112px; }
}
</style>
