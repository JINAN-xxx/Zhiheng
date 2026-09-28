import assert from 'node:assert/strict'

import {
  buildCredentialValues,
  buildQuickResourceIdentity,
  buildRuntimeStatusRows,
  buildPanelState,
  buildSafeResourcePath,
  buildSecretBodyRequest,
  canPublishPlatformModel,
  collectAllPages,
  createRequestGate,
  defaultModelCapabilities,
  getBindingStatusMeta,
  getCapabilityMeta,
  getLifecycleAction,
  getLifecycleStatusMeta,
  getLastAvailablePage,
  getQuickOnboardingStep,
  hasAllPermissions,
  hasPermission,
  isManagementMisconfiguredError,
  normalizeCredential,
  normalizeDeployment,
  normalizePlatformModel,
  normalizeProvider,
  normalizeProviderField,
  curateModelProviders,
  resolveProvider,
  reconcileDeployment,
  shouldUpdateBinding,
  validateCredentialFileMetadata,
  validateCredentialFileText,
  isCredentialCompatible,
} from '../../plugins/ai/utils/modelEngine.js'

assert.deepEqual(
  buildQuickResourceIdentity({ provider: 'OpenAI', upstreamModel: 'openai/gpt-4o', suffix: 'a1b2c3d4' }),
  {
    credentialName: 'quick-openai-a1b2c3d4',
    deploymentAlias: 'quick-openai-gpt-4o-a1b2c3d4',
    modelCode: 'openai-gpt-4o',
    displayName: 'gpt-4o',
  },
)

for (const input of [
  { provider: '', upstreamModel: '', suffix: '' },
  { provider: '中文 Provider', upstreamModel: '深度/模型 一', suffix: '本地' },
  { provider: ' Open AI ', upstreamModel: ' vendor / chat model ', suffix: ' local value ' },
  { provider: 'Open\u0000AI', upstreamModel: 'vendor\u0085/model\u2028name', suffix: '\u0007suffix' },
  { provider: 'p'.repeat(200), upstreamModel: `vendor/${'m'.repeat(200)}`, suffix: 's'.repeat(200) },
]) {
  const identity = buildQuickResourceIdentity(input)
  for (const key of ['credentialName', 'deploymentAlias', 'modelCode']) {
    assert.match(identity[key], /^[a-z0-9][a-z0-9._-]*$/)
    assert.ok(identity[key].length <= 100)
  }
  assert.ok(identity.displayName.length > 0)
  assert.ok(identity.displayName.length <= 100)
}

assert.deepEqual(getQuickOnboardingStep('credential'), { index: 0, title: '连接信息' })
assert.deepEqual(getQuickOnboardingStep('deployment'), { index: 1, title: '测试模型' })
assert.deepEqual(getQuickOnboardingStep('catalog'), { index: 2, title: '确认接入' })
assert.deepEqual(getQuickOnboardingStep('published'), { index: 3, title: '接入成功' })
assert.deepEqual(getQuickOnboardingStep('unknown'), { index: 0, title: '连接信息' })

const requestGate = createRequestGate()
const firstRequest = requestGate.begin()
const secondRequest = requestGate.begin()
assert.equal(requestGate.isCurrent(firstRequest), false)
assert.equal(requestGate.isCurrent(secondRequest), true)
requestGate.invalidate()
assert.equal(requestGate.isCurrent(secondRequest), false)
requestGate.activate()
const reopenedRequest = requestGate.begin()
assert.equal(requestGate.isCurrent(reopenedRequest), true)

assert.deepEqual(buildCredentialValues(
  [{ key: 'api_key' }, { key: 'region' }, { key: 'missing' }],
  { api_key: 'secret', region: '', missing: null, ignored: 'value' },
), { api_key: 'secret' })
assert.deepEqual(buildCredentialValues([], { api_key: 'secret' }), {})

for (const file of [
  { name: 'service-account.json', type: 'application/json', size: 128 },
  { name: 'private.pem', type: 'application/x-pem-file', size: 128 },
  { name: 'credentials.txt', type: 'text/plain', size: 128 },
  { name: 'credentials', type: '', size: 128 },
]) {
  assert.equal(validateCredentialFileMetadata(file), null)
}
assert.match(validateCredentialFileMetadata({ name: 'large.json', type: 'application/json', size: 8193 }), /8192/)
assert.match(validateCredentialFileMetadata({ name: 'secret.png', type: 'image/png', size: 128 }), /文本/)
assert.match(validateCredentialFileMetadata({ name: 'secret.pdf', type: '', size: 128 }), /文本/)
assert.match(validateCredentialFileMetadata({ name: 'credential.dat', type: 'application/x-executable', size: 128 }), /文本/)
assert.match(validateCredentialFileMetadata({ name: 'credential.dat', type: '', size: 128 }), /文本/)
assert.match(validateCredentialFileMetadata({ name: 'credential.txt', type: 'application/x-unknown', size: 128 }), /文本/)
assert.equal(validateCredentialFileMetadata({ name: 'oci_key', type: '', size: 128 }), null)
assert.equal(validateCredentialFileText('a'.repeat(8192)), null)
assert.match(validateCredentialFileText('\u4e2d'.repeat(2731)), /8192/)

assert.equal(isCredentialCompatible({ name: 'openai-main', provider: 'openai' }, 'openai'), true)
assert.equal(isCredentialCompatible({ name: 'openai-main', provider: 'openai' }, 'azure'), false)
const openAiProvider = normalizeProvider({
  provider: 'OpenAI',
  displayName: 'OpenAI',
  litellmProvider: 'openai',
  credentialFields: [{ fieldKey: 'api_key', fieldType: 'password' }],
})
assert.equal(resolveProvider([openAiProvider], 'OpenAI'), openAiProvider)
assert.equal(resolveProvider([openAiProvider], 'openai'), openAiProvider)
assert.equal(resolveProvider([openAiProvider], 'openai').fields[0].key, 'api_key')
assert.equal(isCredentialCompatible({ name: 'openai-main', provider: 'openai' }, 'OpenAI', [openAiProvider]), true)
assert.equal(isCredentialCompatible({ name: 'openai-main', provider: 'openai' }, 'unknown', [openAiProvider]), false)
const ambiguousProviders = [openAiProvider, { ...openAiProvider, provider: 'OpenAI-Compatible' }]
assert.equal(resolveProvider(ambiguousProviders, 'openai'), null)
assert.equal(isCredentialCompatible({ name: 'openai-main', provider: 'openai' }, 'openai', ambiguousProviders), false)

const curatedProviders = curateModelProviders([
  openAiProvider,
  normalizeProvider({ provider: 'Deepseek', displayName: 'Deepseek', litellmProvider: 'deepseek', credentialFields: [] }),
  normalizeProvider({
    provider: 'OpenAI_Compatible', displayName: 'OpenAI-Compatible Endpoints', litellmProvider: 'openai',
    credentialFields: [
      { fieldKey: 'api_base', label: 'API Base', fieldType: 'text' },
      { fieldKey: 'api_key', label: 'API Key', fieldType: 'password' },
    ],
  }),
  normalizeProvider({ provider: 'Bedrock', displayName: 'Bedrock', litellmProvider: 'bedrock', credentialFields: [] }),
])
assert.deepEqual(curatedProviders.map((provider) => provider.provider), ['Deepseek', 'OpenAI_Compatible', 'OpenAI'])
assert.equal(curatedProviders[1].displayName, '硅基流动')
assert.equal(curatedProviders[1].fields.find((field) => field.key === 'api_base')?.defaultValue, 'https://api.siliconflow.cn/v1')

const domesticProviderIds = ['Deepseek', 'Dashscope', 'VolcEngine', 'OpenAI_Compatible', 'MOONSHOT', 'MINIMAX', 'CUSTOM_OPENAI', 'OpenAI', 'Anthropic', 'Google_AI_Studio']
const domesticProviders = domesticProviderIds.map((provider) => normalizeProvider({
  provider,
  displayName: provider,
  litellmProvider: provider.toLowerCase(),
  credentialFields: [],
}))
assert.deepEqual(curateModelProviders(domesticProviders).map((provider) => provider.displayName), [
  'DeepSeek', '阿里云百炼 / 通义千问', '火山引擎 / 豆包', '硅基流动', 'Moonshot / Kimi', 'MiniMax', 'OpenAI', 'Anthropic', 'Google Gemini', '自定义 OpenAI 兼容接口',
])

const requestedDeployment = {
  modelAlias: 'chat-default', provider: 'openai', upstreamModel: 'gpt-4.1-mini', credentialName: 'openai-main',
}
const existingDeployment = normalizeDeployment({
  deploymentId: 'dep_001', modelAlias: 'chat-default', provider: 'openai', upstreamModel: 'gpt-4.1-mini', credentialName: 'openai-main',
})
assert.deepEqual(reconcileDeployment([existingDeployment], requestedDeployment), { kind: 'match', deployment: existingDeployment })
assert.equal(reconcileDeployment([{ ...existingDeployment, provider: 'azure' }], requestedDeployment).kind, 'conflict')
assert.deepEqual(reconcileDeployment([], requestedDeployment), { kind: 'missing', deployment: null })
const configuredDeployment = {
  ...existingDeployment,
  timeout: 30,
  streamTimeout: 45,
  maxRetries: 2,
  rpm: 100,
  tpm: 2000,
  description: 'production route',
}
assert.equal(reconcileDeployment([configuredDeployment], requestedDeployment).kind, 'match')
assert.equal(reconcileDeployment([configuredDeployment], { ...requestedDeployment, timeout: 30 }).kind, 'match')
for (const [field, value] of [
  ['timeout', 31],
  ['streamTimeout', 46],
  ['maxRetries', 3],
  ['rpm', 101],
  ['tpm', 2001],
  ['description', 'different route'],
]) {
  assert.equal(
    reconcileDeployment([configuredDeployment], { ...requestedDeployment, [field]: value }).kind,
    'conflict',
    `${field} mismatch must not reuse deployment`,
  )
}
assert.equal(reconcileDeployment([{ ...existingDeployment, blocked: true }], requestedDeployment).kind, 'conflict')
assert.equal(
  reconcileDeployment([existingDeployment], { ...requestedDeployment, provider: 'OpenAI' }, [openAiProvider]).kind,
  'match',
)
assert.equal(
  reconcileDeployment([existingDeployment], { ...requestedDeployment, provider: 'openai' }, ambiguousProviders).kind,
  'conflict',
)

assert.equal(getLastAvailablePage({ pageNum: 2, pageSize: 10, total: 10, rowsLength: 0 }), 1)
assert.equal(getLastAvailablePage({ pageNum: 3, pageSize: 10, total: 25, rowsLength: 0 }), 2)
assert.equal(getLastAvailablePage({ pageNum: 2, pageSize: 10, total: 20, rowsLength: 10 }), 2)
assert.equal(getLastAvailablePage({ pageNum: 1, pageSize: 10, total: 0, rowsLength: 0 }), 1)

assert.deepEqual(
  normalizeProviderField({
    fieldKey: 'api_key',
    label: 'API Key',
    placeholder: 'sk-...',
    tooltip: 'Provider token',
    required: true,
    fieldType: 'password',
    options: null,
    defaultValue: 'must-not-prefill',
  }),
  {
    key: 'api_key',
    label: 'API Key',
    placeholder: 'sk-...',
    tooltip: 'Provider token',
    required: true,
    fieldType: 'password',
    options: [],
    defaultValue: null,
  },
)
assert.equal(JSON.stringify(normalizeProviderField({ fieldKey: 'api_key', label: 'API Key', fieldType: 'password', defaultValue: 'secret' })).includes('secret'), false)

assert.equal(normalizeProviderField({ label: 'Missing key', fieldType: 'text' }), null)
assert.deepEqual(normalizeProviderField({ fieldKey: 'custom', label: 'Custom', fieldType: 'unknown', defaultValue: 'unsafe' }), {
  key: 'custom',
  label: 'Custom',
  placeholder: null,
  tooltip: null,
  required: false,
  fieldType: 'text',
  options: [],
  defaultValue: null,
})
for (const fieldKey of [
  'apiKey', 'api_key', 'access-token', 'clientSecret', 'password', 'private_key',
  'aws_access_key_id', 'oci_key', 'vertex_credentials', 'basic_auth', 'client_cert', 'db_pass',
]) {
  assert.equal(
    normalizeProviderField({ fieldKey, label: fieldKey, fieldType: 'text', defaultValue: 'must-not-prefill' }).defaultValue,
    null,
  )
}
assert.equal(normalizeProviderField({ fieldKey: 'region', label: 'Region', fieldType: 'text', defaultValue: 'cn' }).defaultValue, 'cn')
assert.equal(normalizeProviderField({ fieldKey: 'region_file', label: 'Region file', fieldType: 'upload', defaultValue: 'unsafe' }).defaultValue, null)
assert.equal(normalizeProviderField({ fieldKey: 'metadata', label: 'Metadata', fieldType: 'textarea', defaultValue: 'unsafe' }).defaultValue, null)
assert.equal(normalizeProviderField({ fieldKey: 'region', label: 'Region', fieldType: 'select', defaultValue: 'cn' }).defaultValue, 'cn')

assert.equal(hasPermission(['ai:model:list'], 'ai:model:list'), true)
assert.equal(hasPermission(['ai:model:list'], 'ai:model:query'), false)
assert.equal(hasPermission(['*:*:*'], 'ai:model:query'), true)
assert.equal(hasAllPermissions(['ai:model:list', 'ai:model:query'], ['ai:model:list', 'ai:model:query']), true)
assert.equal(hasAllPermissions(['ai:model:list'], ['ai:model:list', 'ai:model:query']), false)

assert.equal(buildSafeResourcePath('/ai/engine/credentials', 'openai main', 'rotate'), '/ai/engine/credentials/openai%20main/rotate')
assert.equal(buildSafeResourcePath('/ai/models', 'mdl/01'), '/ai/models/mdl%2F01')
assert.throws(() => buildSafeResourcePath('/ai/engine/credentials', '.'), /非法资源标识/)
assert.throws(() => buildSafeResourcePath('/ai/engine/credentials', '..'), /非法资源标识/)

const pageCalls = []
const allRows = await collectAllPages(async ({ pageNum, pageSize }) => {
  pageCalls.push(pageNum)
  if (pageNum === 1) return { rows: [{ id: 1 }, { id: 2 }], total: 3 }
  if (pageNum === 2) return { rows: [{ id: 3 }], total: 3 }
  throw new Error('must stop at total')
}, { pageSize: 2 })
assert.deepEqual(allRows, [{ id: 1 }, { id: 2 }, { id: 3 }])
assert.deepEqual(pageCalls, [1, 2])
let emptyPageCalls = 0
assert.deepEqual(await collectAllPages(async () => {
  emptyPageCalls += 1
  return { rows: [], total: 500 }
}), [])
assert.equal(emptyPageCalls, 1)
await assert.rejects(
  () => collectAllPages(async () => ({ rows: [{ id: 1 }], total: 100 }), { pageSize: 1, maxPages: 2 }),
  /分页响应未终止/,
)

assert.equal(isManagementMisconfiguredError({ response: { data: { detail: { code: 'LITELLM_MANAGEMENT_MISCONFIGURED' } } } }), true)
assert.equal(isManagementMisconfiguredError(new Error('network')), false)

const credential = normalizeCredential({
  credentialName: 'openai-main',
  provider: 'openai',
  referencedDeploymentCount: 2,
  createdAt: '2026-08-11T08:00:00Z',
  updatedAt: '2026-08-12T08:00:00Z',
  credentialValues: { api_key: 'credential-secret' },
  apiKey: 'api-secret',
  password: 'password-secret',
  token: 'token-secret',
  secret: 'generic-secret',
})
assert.deepEqual(credential, {
  name: 'openai-main',
  provider: 'openai',
  deploymentCount: 2,
  createdAt: '2026-08-11T08:00:00Z',
  updatedAt: '2026-08-12T08:00:00Z',
})
for (const secret of ['credential-secret', 'api-secret', 'password-secret', 'token-secret', 'generic-secret']) {
  assert.equal(JSON.stringify(credential).includes(secret), false)
}

assert.deepEqual(
  normalizeDeployment({
    model_name: 'legacy-alias',
    model_info: { id: 'dep_legacy', base_model: 'gpt-4.1-mini', updated_at: '2026-08-12T10:00:00Z' },
    litellm_params: { model: 'openai/gpt-4.1-mini', custom_llm_provider: 'openai' },
    blocked: false,
  }),
  {
    deploymentId: 'dep_legacy',
    alias: 'legacy-alias',
    provider: 'openai',
    baseModel: 'gpt-4.1-mini',
    credentialName: null,
    status: 'unknown',
    latencyMs: null,
    blocked: false,
    createdAt: null,
    updatedAt: '2026-08-12T10:00:00Z',
    timeout: null,
    streamTimeout: null,
    maxRetries: null,
    rpm: null,
    tpm: null,
    description: null,
  },
)

assert.deepEqual(
  normalizeDeployment({
    deploymentId: 'dep_current',
    modelAlias: 'chat-default',
    provider: 'openai',
    upstreamModel: 'gpt-4.1-mini',
    credentialName: 'openai-main',
    status: 'healthy',
    latencyMs: 43.2,
    blocked: true,
    createdAt: '2026-08-11T08:00:00Z',
    updatedAt: '2026-08-12T08:00:00Z',
    maxRetries: 2,
  }),
  {
    deploymentId: 'dep_current',
    alias: 'chat-default',
    provider: 'openai',
    baseModel: 'gpt-4.1-mini',
    credentialName: 'openai-main',
    status: 'healthy',
    latencyMs: 43.2,
    blocked: true,
    createdAt: '2026-08-11T08:00:00Z',
    updatedAt: '2026-08-12T08:00:00Z',
    timeout: null,
    streamTimeout: null,
    maxRetries: 2,
    rpm: null,
    tpm: null,
    description: null,
  },
)

assert.deepEqual(getLifecycleStatusMeta('active'), { label: '已发布', type: 'success' })
assert.deepEqual(getLifecycleStatusMeta('draft'), { label: '草稿', type: 'info' })
assert.deepEqual(getLifecycleStatusMeta('disabled'), { label: '已停用', type: 'warning' })
assert.deepEqual(getLifecycleStatusMeta('other'), { label: '未知', type: 'info' })
assert.equal(getLifecycleAction('draft'), '发布')
assert.equal(getLifecycleAction('active'), '停用')
assert.equal(getLifecycleAction('disabled'), '重新启用')
assert.deepEqual(defaultModelCapabilities('chat'), {
  streaming: true,
  vision: false,
  reasoning: false,
  tools: false,
})
assert.deepEqual(defaultModelCapabilities('embedding'), { dimensionsOverride: false })
assert.deepEqual(defaultModelCapabilities('rerank'), { returnDocuments: false })
assert.deepEqual(defaultModelCapabilities('unknown'), {})
assert.deepEqual(defaultModelCapabilities(undefined), {})
assert.deepEqual(getCapabilityMeta('vision'), { label: '视觉', type: 'primary' })
assert.deepEqual(getBindingStatusMeta('active'), { label: '已绑定', type: 'success' })
assert.deepEqual(getBindingStatusMeta(null), { label: '未绑定', type: 'info' })
assert.deepEqual(getBindingStatusMeta('unknown'), { label: '未知', type: 'info' })

const platformModel = normalizePlatformModel({
  modelId: 'mdl_01',
  modelCode: 'chat-pro',
  displayName: 'Chat Pro',
  modelType: 'chat',
  capabilities: { vision: true, reasoning: true, tools: false, streaming: true },
  lifecycleStatus: 'active',
  updateTime: '2026-08-12T11:00:00Z',
  binding: { engineResourceId: 'chat-default', bindingStatus: 'active', version: 3 },
})
assert.deepEqual(platformModel.capabilities.map((item) => item.key), ['vision', 'reasoning', 'streaming'])
assert.equal(platformModel.bindingAlias, 'chat-default')
assert.equal(platformModel.bindingVersion, 3)
assert.equal(platformModel.bindingStatus, 'active')
assert.equal(normalizePlatformModel({ modelId: 'list-only' }).bindingStatus, 'unknown')
assert.equal(normalizePlatformModel({ modelId: 'detail', binding: null }).bindingStatus, 'missing')
assert.equal(normalizePlatformModel({ modelId: 'disabled', binding: { bindingStatus: 'disabled' } }).bindingStatus, 'disabled')
assert.equal(canPublishPlatformModel(platformModel), true)
for (const bindingStatus of ['disabled', 'missing', 'unknown']) {
  assert.equal(canPublishPlatformModel({ ...platformModel, bindingStatus }), false)
}
assert.equal(canPublishPlatformModel({ ...platformModel, bindingAlias: null }), false)

assert.equal(shouldUpdateBinding(null, 'chat-default'), true)
assert.equal(shouldUpdateBinding({ engineResourceId: 'old-route', bindingStatus: 'active' }, 'chat-default'), true)
assert.equal(shouldUpdateBinding({ engineResourceId: 'chat-default', bindingStatus: 'disabled' }, 'chat-default'), true)
assert.equal(shouldUpdateBinding({ engineResourceId: 'chat-default', bindingStatus: 'active' }, 'chat-default'), false)

const secretRequest = buildSecretBodyRequest({ values: { api_key: 'request-secret' } })
assert.deepEqual(secretRequest, {
  data: { values: { api_key: 'request-secret' } },
  headers: { repeatSubmit: false },
})
assert.equal('params' in secretRequest, false)

assert.deepEqual(buildPanelState({ configured: false }), {
  kind: 'not-configured',
  title: 'Management Key 未配置',
  retryable: false,
})
assert.deepEqual(buildPanelState({ configured: true, error: new Error('network') }), {
  kind: 'error',
  title: '加载失败',
  retryable: true,
})
assert.deepEqual(buildPanelState({ configured: true, error: new Error('network'), items: [{ id: 1 }] }), {
  kind: 'stale',
  title: '刷新失败，数据可能已过期',
  retryable: true,
})
assert.deepEqual(buildPanelState({ configured: true, loading: false, items: [] }), {
  kind: 'empty',
  title: '暂无数据',
  retryable: true,
})

const runtimeRows = buildRuntimeStatusRows({
  catalog: { ok: true, latencyMs: 17, checkedAt: '2026-08-12T08:00:00Z' },
  engine: { ok: false, latencyMs: 31, checkedAt: '2026-08-12T08:00:01Z' },
})
assert.equal(runtimeRows.length, 6)
assert.deepEqual(runtimeRows.slice(0, 2).map(({ name, label, latency, checkedAt }) => ({ name, label, latency, checkedAt })), [
  { name: 'Model Service', label: '检查失败', latency: 31, checkedAt: '2026-08-12T08:00:01Z' },
  { name: '本地 Database', label: '正常', latency: 17, checkedAt: '2026-08-12T08:00:00Z' },
])
assert.deepEqual(runtimeRows.slice(2).map(({ label }) => label), ['检查失败', '检查失败', '检查失败', '检查失败'])
assert.equal(runtimeRows.slice(2).some(({ label }) => label === '未配置'), false)

const configuredRuntimeRows = buildRuntimeStatusRows({
  catalog: { ok: false, latencyMs: 11, checkedAt: '2026-08-12T08:00:00Z' },
  engine: {
    ok: true,
    latencyMs: 23,
    checkedAt: '2026-08-12T08:00:01Z',
    data: {
      runtimeConfigured: false,
      runtimeAvailable: false,
      managementConfigured: true,
      managementAvailable: true,
      latencyMs: 9,
      lastCheckedAt: '2026-08-12T07:59:59Z',
    },
  },
})
assert.deepEqual(configuredRuntimeRows.map(({ label }) => label), ['正常', '检查失败', '未配置', '已配置', '未配置', '正常'])
assert.equal(configuredRuntimeRows[0].latency, 23)
assert.equal(configuredRuntimeRows[5].latency, 9)
