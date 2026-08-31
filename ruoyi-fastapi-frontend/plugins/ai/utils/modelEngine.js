const PROVIDER_FIELD_TYPES = new Set(['text', 'password', 'select', 'upload', 'textarea'])

const CURATED_PROVIDER_PROFILES = Object.freeze([
  Object.freeze({ provider: 'Deepseek', displayName: 'DeepSeek' }),
  Object.freeze({ provider: 'Dashscope', displayName: '阿里云百炼 / 通义千问' }),
  Object.freeze({ provider: 'VolcEngine', displayName: '火山引擎 / 豆包' }),
  Object.freeze({ provider: 'OpenAI_Compatible', displayName: '硅基流动', apiBase: 'https://api.siliconflow.cn/v1' }),
  Object.freeze({ provider: 'MOONSHOT', displayName: 'Moonshot / Kimi' }),
  Object.freeze({ provider: 'MINIMAX', displayName: 'MiniMax' }),
  Object.freeze({ provider: 'Anthropic', displayName: 'Anthropic' }),
  Object.freeze({ provider: 'Google_AI_Studio', displayName: 'Google Gemini' }),
  Object.freeze({ provider: 'Azure', displayName: 'Azure OpenAI' }),
  Object.freeze({ provider: 'Openrouter', displayName: 'OpenRouter' }),
  Object.freeze({ provider: 'Groq', displayName: 'Groq' }),
  Object.freeze({ provider: 'MistralAI', displayName: 'Mistral AI' }),
  Object.freeze({ provider: 'Cohere', displayName: 'Cohere' }),
  Object.freeze({ provider: 'CUSTOM_OPENAI', displayName: '自定义 OpenAI 兼容接口' }),
])

const CAPABILITY_META = Object.freeze({
  vision: Object.freeze({ label: '视觉', type: 'primary' }),
  reasoning: Object.freeze({ label: '推理', type: 'warning' }),
  tools: Object.freeze({ label: '工具', type: 'success' }),
  streaming: Object.freeze({ label: '流式', type: 'info' }),
})

const LIFECYCLE_META = Object.freeze({
  active: Object.freeze({ label: '已发布', type: 'success' }),
  draft: Object.freeze({ label: '草稿', type: 'info' }),
  disabled: Object.freeze({ label: '已停用', type: 'warning' }),
})

const LIFECYCLE_ACTION = Object.freeze({
  active: '停用',
  draft: '发布',
  disabled: '重新启用',
})

const BINDING_META = Object.freeze({
  active: Object.freeze({ label: '已绑定', type: 'success' }),
  disabled: Object.freeze({ label: '已禁用', type: 'warning' }),
  missing: Object.freeze({ label: '未绑定', type: 'info' }),
  unknown: Object.freeze({ label: '未知', type: 'info' }),
})

const asString = (value, fallback = '') => (typeof value === 'string' ? value : fallback)
const asNullableString = (value) => (typeof value === 'string' && value.length > 0 ? value : null)
const asNullableNumber = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : null)

const QUICK_STEP_META = Object.freeze({
  credential: Object.freeze({ index: 0, title: '连接信息' }),
  deployment: Object.freeze({ index: 1, title: '测试模型' }),
  catalog: Object.freeze({ index: 2, title: '确认接入' }),
  published: Object.freeze({ index: 3, title: '接入成功' }),
})

const safeResourcePart = (value, fallback) => {
  const normalized = asString(value).toLowerCase()
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, '')
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[._-]+|[._-]+$/g, '')
  return (normalized || fallback).slice(0, 64)
}

export function buildQuickResourceIdentity({ provider, upstreamModel, suffix }) {
  const providerPart = safeResourcePart(provider, 'provider')
  const modelPart = safeResourcePart(upstreamModel, 'model')
  const suffixPart = safeResourcePart(suffix, 'local').slice(0, 16)
  const displayName = (asString(upstreamModel).split('/').filter(Boolean).at(-1)?.trim() || 'AI 模型').slice(0, 100)
  return {
    credentialName: `quick-${providerPart}-${suffixPart}`.slice(0, 100),
    deploymentAlias: `quick-${modelPart}-${suffixPart}`.slice(0, 100),
    modelCode: modelPart.slice(0, 100),
    displayName,
  }
}

export const getQuickOnboardingStep = (state) => QUICK_STEP_META[state] || QUICK_STEP_META.credential

export const MAX_CREDENTIAL_VALUE_BYTES = 8192

export function createRequestGate() {
  let generation = 0
  let active = true
  return {
    begin() {
      generation += 1
      return generation
    },
    isCurrent(requestGeneration) {
      return active && requestGeneration === generation
    },
    invalidate() {
      active = false
      generation += 1
    },
    activate() {
      active = true
    },
  }
}

export function buildCredentialValues(fields, sourceValues) {
  const values = {}
  for (const field of Array.isArray(fields) ? fields : []) {
    const value = sourceValues?.[field?.key]
    if (typeof value === 'string' && value.length > 0) values[field.key] = value
  }
  return values
}

const CREDENTIAL_TEXT_EXTENSIONS = new Set([
  'cer', 'conf', 'config', 'crt', 'json', 'key', 'pem', 'txt', 'yaml', 'yml',
])

const CREDENTIAL_TEXT_MIME_TYPES = new Set([
  'application/json', 'application/pem-certificate-chain', 'application/x-pem-file',
  'application/x-yaml', 'application/yaml',
])

export function validateCredentialFileMetadata(file) {
  if (Number(file?.size) > MAX_CREDENTIAL_VALUE_BYTES) return `文件不能超过 ${MAX_CREDENTIAL_VALUE_BYTES} 字节`
  const type = asString(file?.type).toLowerCase().split(';', 1)[0].trim()
  const fileName = asString(file?.name).toLowerCase()
  const extension = fileName.includes('.') ? fileName.split('.').at(-1) : ''
  if (extension && !CREDENTIAL_TEXT_EXTENSIONS.has(extension)) return '仅支持文本、JSON 或 PEM 凭据文件'
  if (type && !type.startsWith('text/') && !CREDENTIAL_TEXT_MIME_TYPES.has(type)) return '仅支持文本、JSON 或 PEM 凭据文件'
  return null
}

export function validateCredentialFileText(value) {
  if (typeof value !== 'string') return '凭据文件读取失败'
  if (new TextEncoder().encode(value).byteLength > MAX_CREDENTIAL_VALUE_BYTES) {
    return `文件内容不能超过 ${MAX_CREDENTIAL_VALUE_BYTES} 字节`
  }
  return null
}

export function normalizeProviderField(field) {
  if (!field || typeof field !== 'object') return null
  const key = asString(field.fieldKey || field.key).trim()
  if (!key) return null

  const isKnownFieldType = PROVIDER_FIELD_TYPES.has(field.fieldType)
  const fieldType = isKnownFieldType ? field.fieldType : 'text'
  const normalizedKey = key.toLowerCase().replace(/[^a-z0-9]/g, '')
  const isSensitive = ['key', 'credential', 'cred', 'auth', 'token', 'secret', 'password', 'pass', 'private', 'cert']
    .some((marker) => normalizedKey.includes(marker))
  const neverPrefill = !isKnownFieldType || ['password', 'upload', 'textarea'].includes(fieldType) || isSensitive
  return {
    key,
    label: asString(field.label, key),
    placeholder: asNullableString(field.placeholder),
    tooltip: asNullableString(field.tooltip),
    required: field.required === true,
    fieldType,
    options: Array.isArray(field.options) ? [...field.options] : [],
    defaultValue: neverPrefill ? null : field.defaultValue ?? null,
  }
}

export function hasPermission(permissions, permission) {
  return Array.isArray(permissions) && (permissions.includes('*:*:*') || permissions.includes(permission))
}

export function hasAllPermissions(permissions, requiredPermissions) {
  return Array.isArray(requiredPermissions) && requiredPermissions.every((permission) => hasPermission(permissions, permission))
}

export function buildSafeResourcePath(basePath, resourceId, suffix = '') {
  const segment = typeof resourceId === 'string' ? resourceId : String(resourceId ?? '')
  if (!segment || segment === '.' || segment === '..') throw new TypeError('非法资源标识')
  const path = `${basePath.replace(/\/$/, '')}/${encodeURIComponent(segment)}`
  return suffix ? `${path}/${encodeURIComponent(suffix)}` : path
}

export async function collectAllPages(fetchPage, { pageSize = 100, maxPages = 1000 } = {}) {
  const rows = []
  for (let pageNum = 1; pageNum <= maxPages; pageNum += 1) {
    const response = await fetchPage({ pageNum, pageSize })
    const pageRows = Array.isArray(response?.rows) ? response.rows : []
    rows.push(...pageRows)
    const total = Number(response?.total)
    if (pageRows.length === 0 || pageRows.length < pageSize || (Number.isFinite(total) && total >= 0 && rows.length >= total)) return rows
  }
  throw new Error('分页响应未终止')
}

export function isManagementMisconfiguredError(error) {
  const detail = error?.response?.data?.detail
  const code = detail?.code || error?.response?.data?.code || error?.code
  return code === 'LITELLM_MANAGEMENT_MISCONFIGURED'
}

export function buildSecretBodyRequest(data) {
  return { data, headers: { repeatSubmit: false } }
}

export function normalizeProvider(provider) {
  if (!provider || typeof provider !== 'object') return null
  const providerId = asString(provider.provider).trim()
  if (!providerId) return null
  return {
    provider: providerId,
    displayName: asString(provider.displayName, providerId),
    engineProvider: asString(provider.litellmProvider, providerId),
    defaultModelPlaceholder: asNullableString(provider.defaultModelPlaceholder),
    fields: Array.isArray(provider.credentialFields)
      ? provider.credentialFields.map(normalizeProviderField).filter(Boolean)
      : [],
  }
}

export function curateModelProviders(providers) {
  const providerMap = new Map(
    (Array.isArray(providers) ? providers : [])
      .filter((provider) => provider?.provider)
      .map((provider) => [provider.provider, provider]),
  )
  return CURATED_PROVIDER_PROFILES.flatMap((profile) => {
    const provider = providerMap.get(profile.provider)
    if (!provider) return []
    const fields = provider.fields.map((field) => (
      profile.apiBase && field.key === 'api_base'
        ? { ...field, defaultValue: profile.apiBase }
        : field
    ))
    return [{ ...provider, displayName: profile.displayName, fields }]
  })
}

export function resolveProvider(providers, identity) {
  if (typeof identity !== 'string' || !identity) return null
  const matches = (Array.isArray(providers) ? providers : []).filter((provider) => (
    provider?.provider === identity || provider?.engineProvider === identity
  ))
  return matches.length === 1 ? matches[0] : null
}

export function resolveEngineProvider(providers, identity) {
  return resolveProvider(providers, identity)?.engineProvider || null
}

export function normalizeCredential(credential) {
  const source = credential && typeof credential === 'object' ? credential : {}
  return {
    name: asString(source.credentialName || source.credential_name),
    provider: asString(source.provider || source.credential_info?.provider),
    deploymentCount: asNullableNumber(
      source.referencedDeploymentCount ?? source.referenced_deployment_count ?? source.deploymentCount,
    ) ?? 0,
    createdAt: asNullableString(source.createdAt ?? source.created_at),
    updatedAt: asNullableString(source.updatedAt ?? source.updated_at),
  }
}

function legacyBaseModel(source) {
  const explicit = source.model_info?.base_model
  if (typeof explicit === 'string' && explicit) return explicit
  const model = asString(source.litellm_params?.model)
  const separator = model.indexOf('/')
  return separator >= 0 ? model.slice(separator + 1) : model
}

export function normalizeDeployment(deployment) {
  const source = deployment && typeof deployment === 'object' ? deployment : {}
  const legacyInfo = source.model_info && typeof source.model_info === 'object' ? source.model_info : {}
  const legacyParams = source.litellm_params && typeof source.litellm_params === 'object' ? source.litellm_params : {}
  return {
    deploymentId: asString(source.deploymentId || legacyInfo.id),
    alias: asString(source.modelAlias || source.model_name),
    provider: asString(source.provider || legacyParams.custom_llm_provider),
    baseModel: asString(source.upstreamModel || legacyBaseModel(source)),
    credentialName: asNullableString(source.credentialName || legacyParams.credential_name),
    status: asString(source.status, 'unknown'),
    latencyMs: asNullableNumber(source.latencyMs ?? source.latency_ms),
    blocked: source.blocked === true,
    createdAt: asNullableString(source.createdAt ?? source.created_at ?? legacyInfo.created_at),
    updatedAt: asNullableString(source.updatedAt ?? source.updated_at ?? legacyInfo.updated_at),
    timeout: asNullableNumber(source.timeout),
    streamTimeout: asNullableNumber(source.streamTimeout ?? source.stream_timeout),
    maxRetries: asNullableNumber(source.maxRetries ?? source.max_retries),
    rpm: asNullableNumber(source.rpm),
    tpm: asNullableNumber(source.tpm),
    description: asNullableString(source.description),
  }
}

export function isCredentialCompatible(credential, provider, providers = []) {
  const engineProvider = providers.length ? resolveEngineProvider(providers, provider) : provider
  return Boolean(engineProvider && credential && credential.provider === engineProvider)
}

export function reconcileDeployment(deployments, payload, providers = []) {
  const normalized = (Array.isArray(deployments) ? deployments : []).map((item) => (
    item?.alias !== undefined ? item : normalizeDeployment(item)
  ))
  const aliases = normalized.filter((item) => item.alias === payload?.modelAlias)
  const engineProvider = providers.length ? resolveEngineProvider(providers, payload?.provider) : payload?.provider
  const deployment = aliases.find((item) => (
    item.blocked !== true
    && Boolean(engineProvider)
    && item.provider === engineProvider
    && item.baseModel === payload?.upstreamModel
    && (item.credentialName || '') === (payload?.credentialName || '')
    && ['timeout', 'streamTimeout', 'maxRetries', 'rpm', 'tpm', 'description'].every((field) => (
      !Object.prototype.hasOwnProperty.call(payload || {}, field)
      || payload[field] === undefined
      || item[field] === payload[field]
    ))
  ))
  if (deployment) return { kind: 'match', deployment }
  if (aliases.length) return { kind: 'conflict', deployment: aliases[0] }
  return { kind: 'missing', deployment: null }
}

export function getLastAvailablePage({ pageNum, pageSize, total, rowsLength }) {
  const currentPage = Math.max(1, Number(pageNum) || 1)
  if (currentPage === 1) return 1
  const normalizedPageSize = Math.max(1, Number(pageSize) || 1)
  const normalizedTotal = Math.max(0, Number(total) || 0)
  const lastPage = Math.max(1, Math.ceil(normalizedTotal / normalizedPageSize))
  if (currentPage > lastPage || Number(rowsLength) === 0) return Math.min(lastPage, currentPage - 1)
  return currentPage
}

export function getLifecycleStatusMeta(status) {
  return LIFECYCLE_META[status] || { label: '未知', type: 'info' }
}

export function getLifecycleAction(status) {
  return LIFECYCLE_ACTION[status] || ''
}

export function getCapabilityMeta(capability) {
  return CAPABILITY_META[capability] || { label: capability || '未知', type: 'info' }
}

export function defaultModelCapabilities(type) {
  if (type === 'chat') return { streaming: true, vision: false, reasoning: false, tools: false }
  if (type === 'embedding') return { dimensionsOverride: false }
  if (type === 'rerank') return { returnDocuments: false }
  return {}
}

export function getBindingStatusMeta(status) {
  return BINDING_META[status] || BINDING_META.missing
}

export function shouldUpdateBinding(binding, engineResourceId) {
  if (!binding || typeof binding !== 'object') return true
  return binding.engineResourceId !== engineResourceId || binding.bindingStatus !== 'active'
}

export function normalizePlatformModel(model) {
  const source = model && typeof model === 'object' ? model : {}
  const capabilityValues = source.capabilities && typeof source.capabilities === 'object' ? source.capabilities : {}
  const hasBindingDetail = Object.prototype.hasOwnProperty.call(source, 'binding')
  const binding = source.binding && typeof source.binding === 'object' ? source.binding : null
  return {
    modelId: asString(source.modelId),
    modelCode: asString(source.modelCode),
    displayName: asString(source.displayName),
    modelType: asString(source.modelType),
    capabilities: Object.keys(CAPABILITY_META)
      .filter((key) => capabilityValues[key] === true)
      .map((key) => ({ key, ...getCapabilityMeta(key) })),
    bindingAlias: asNullableString(binding?.engineResourceId),
    bindingStatus: hasBindingDetail ? binding?.bindingStatus || 'missing' : 'unknown',
    bindingVersion: asNullableNumber(binding?.version),
    lifecycleStatus: asString(source.lifecycleStatus),
    updatedAt: asNullableString(source.updateTime),
  }
}

export function canPublishPlatformModel(model) {
  return Boolean(model?.bindingAlias) && model?.bindingStatus === 'active'
}

export function buildPanelState({ configured = true, loading = false, error = null, items = null } = {}) {
  if (!configured) {
    return { kind: 'not-configured', title: 'Management Key 未配置', retryable: false }
  }
  if (error) {
    if (Array.isArray(items) && items.length > 0) {
      return { kind: 'stale', title: '刷新失败，数据可能已过期', retryable: true }
    }
    return { kind: 'error', title: '加载失败', retryable: true }
  }
  if (loading) {
    return { kind: 'loading', title: '加载中', retryable: false }
  }
  if (Array.isArray(items) && items.length === 0) {
    return { kind: 'empty', title: '暂无数据', retryable: true }
  }
  return { kind: 'ready', title: '', retryable: true }
}

const failedStatus = (name, source) => ({
  name,
  label: '检查失败',
  type: 'info',
  latency: source?.latencyMs ?? null,
  checkedAt: source?.checkedAt ?? null,
})

const engineStatus = (name, engine, configuredKey, availableKey, configuredOnly = false) => {
  if (!engine?.ok) return failedStatus(name, engine)
  const configured = engine.data?.[configuredKey] === true
  const available = engine.data?.[availableKey] === true
  if (!configured) {
    return { name, label: '未配置', type: 'warning', latency: null, checkedAt: engine.checkedAt }
  }
  if (configuredOnly) {
    return { name, label: '已配置', type: 'success', latency: null, checkedAt: engine.checkedAt }
  }
  return {
    name,
    label: available ? '正常' : '不可用',
    type: available ? 'success' : 'danger',
    latency: engine.data?.latencyMs ?? null,
    checkedAt: engine.checkedAt,
  }
}

export function buildRuntimeStatusRows({ catalog = {}, engine = {} } = {}) {
  return [
    engine.ok
      ? { name: 'Model Service', label: '正常', type: 'success', latency: engine.latencyMs ?? null, checkedAt: engine.checkedAt ?? null }
      : failedStatus('Model Service', engine),
    catalog.ok
      ? { name: '本地 Database', label: '正常', type: 'success', latency: catalog.latencyMs ?? null, checkedAt: catalog.checkedAt ?? null }
      : failedStatus('本地 Database', catalog),
    engineStatus('Runtime Key', engine, 'runtimeConfigured', 'runtimeAvailable', true),
    engineStatus('Management Key', engine, 'managementConfigured', 'managementAvailable', true),
    engineStatus('LiteLLM Runtime', engine, 'runtimeConfigured', 'runtimeAvailable'),
    engineStatus('LiteLLM Management', engine, 'managementConfigured', 'managementAvailable'),
  ]
}
