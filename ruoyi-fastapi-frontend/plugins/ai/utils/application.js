function text(value, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function optionalText(value) {
  return typeof value === 'string' && value ? value : null
}

function positiveInteger(value, fallback = 1) {
  return Number.isInteger(value) && value > 0 ? value : fallback
}

function safeSegment(value) {
  if (typeof value !== 'string' || !value || value === '.' || value === '..' || /[\u0000-\u001f\u007f]/u.test(value)) {
    throw new Error('资源标识不合法')
  }
  return encodeURIComponent(value).replaceAll('.', '%2E')
}

export function buildApplicationPath(applicationId, suffix = '') {
  const path = ['/ai/applications', safeSegment(applicationId)]
  if (suffix) path.push(...suffix.split('/').filter(Boolean).map(safeSegment))
  return path.join('/')
}

export function buildDeveloperKeyPayload(form = {}) {
  const quota = { warningRatio: '0.8', hardLimitRatio: '1', currency: 'USD' }
  if (form.rpmLimit) quota.rpmLimit = form.rpmLimit
  if (form.tpmLimit) quota.tpmLimit = form.tpmLimit
  if (form.concurrencyLimit) quota.concurrencyLimit = form.concurrencyLimit
  const expiresAt = form.expiresAt instanceof Date ? form.expiresAt.toISOString() : null
  return {
    keyName: text(form.keyName),
    modelIds: Array.isArray(form.modelIds) ? [...form.modelIds] : [],
    expiresAt,
    quota,
  }
}

export function shouldResetDeveloperKeys(currentApplicationId, nextApplicationId) {
  return currentApplicationId !== nextApplicationId
}

export function normalizeApplication(source = {}) {
  source = source && typeof source === 'object' ? source : {}
  return {
    applicationId: text(source.applicationId ?? source.application_id),
    applicationCode: text(source.applicationCode ?? source.application_code),
    applicationName: text(source.applicationName ?? source.application_name, '未命名应用'),
    status: text(source.status, 'unknown'),
    version: positiveInteger(source.version),
    createdAt: optionalText(source.createdAt ?? source.created_at),
    updatedAt: optionalText(source.updatedAt ?? source.updated_at),
  }
}

export function normalizeApplicationPage(source = {}) {
  const payload = source?.data && !Array.isArray(source.data) ? source.data : source
  const rows = Array.isArray(payload?.rows) ? payload.rows : []
  const total = Number.isInteger(payload?.total) && payload.total >= 0 ? payload.total : rows.length
  return { rows: rows.map(normalizeApplication), total }
}

export function normalizeApplicationModelPage(source = {}) {
  const payload = source?.data && !Array.isArray(source.data) ? source.data : source
  const rows = Array.isArray(payload?.rows) ? payload.rows : []
  const total = Number.isInteger(payload?.total) && payload.total >= 0 ? payload.total : rows.length
  return { rows, total }
}

function normalizeGrant(source = {}) {
  return {
    grantId: text(source.grantId ?? source.grant_id),
    modelId: text(source.modelId ?? source.model_id),
    status: text(source.status, 'unknown'),
  }
}

function normalizeQuota(source = {}) {
  return {
    rpmLimit: source.rpmLimit ?? source.rpm_limit ?? null,
    tpmLimit: source.tpmLimit ?? source.tpm_limit ?? null,
    concurrencyLimit: source.concurrencyLimit ?? source.concurrency_limit ?? null,
    monthlyBudget: source.monthlyBudget ?? source.monthly_budget ?? null,
    currency: text(source.currency, 'USD'),
  }
}

export function normalizeDeveloperKey(source = {}) {
  return {
    keyId: text(source.keyId ?? source.key_id),
    applicationId: text(source.applicationId ?? source.application_id),
    keyName: text(source.keyName ?? source.key_name, '未命名密钥'),
    maskedKey: text(source.maskedKey ?? source.masked_key),
    fingerprint: text(source.fingerprint),
    status: text(source.status, 'unknown'),
    expiresAt: optionalText(source.expiresAt ?? source.expires_at),
    retireAt: optionalText(source.retireAt ?? source.retire_at),
    lastUsedAt: optionalText(source.lastUsedAt ?? source.last_used_at),
    createdAt: optionalText(source.createdAt ?? source.created_at),
    grants: Array.isArray(source.grants) ? source.grants.map(normalizeGrant) : [],
    quota: normalizeQuota(source.quota),
  }
}

export function normalizeDeveloperKeys(source = {}) {
  const rows = Array.isArray(source) ? source : Array.isArray(source?.data) ? source.data : source?.rows || []
  return rows.filter(Boolean).map(normalizeDeveloperKey)
}

const APPLICATION_TABS = new Set(['overview', 'keys', 'models', 'usage'])

export function normalizeApplicationTab(value) {
  const tab = Array.isArray(value) ? value[0] : value
  return APPLICATION_TABS.has(tab) ? tab : 'overview'
}

export function applicationTabResources(value) {
  return {
    overview: [],
    keys: ['keys'],
    models: ['keys', 'models'],
    usage: ['usage'],
  }[normalizeApplicationTab(value)]
}

export function isCallablePlatformModel(model = {}) {
  const source = model && typeof model === 'object' ? model : {}
  const lifecycleStatus = source.lifecycleStatus ?? source.lifecycle_status
  const publishedAt = source.publishedAt ?? source.published_at
  return lifecycleStatus === 'active' && Boolean(publishedAt)
}

export async function resolvePreferredApplication(rows = [], preferredId, fetchApplication) {
  const fallback = rows[0] || null
  const applicationId = Array.isArray(preferredId) ? preferredId[0] : preferredId
  if (typeof applicationId !== 'string' || !applicationId) return fallback
  if (applicationId === '.' || applicationId === '..' || /[\u0000-\u001f\u007f]/u.test(applicationId)) return fallback
  const pageApplication = rows.find((item) => item.applicationId === applicationId)
  if (pageApplication) return pageApplication
  try {
    const response = await fetchApplication(applicationId)
    const application = normalizeApplication(response?.data ?? response)
    return application.applicationId === applicationId ? application : fallback
  } catch (reason) {
    const status = reason?.response?.status ?? reason?.status
    if (status === 404) return fallback
    throw reason
  }
}

export function aggregateGrantedModels(keys = [], models = []) {
  const modelMap = new Map(models.map((model) => {
    const modelId = text(model.modelId ?? model.model_id)
    return [modelId, {
      modelId,
      modelName: text(model.modelName ?? model.model_name ?? model.displayName ?? model.display_name ?? model.title, '未命名模型'),
      modelCode: text(model.modelCode ?? model.model_code, modelId),
      available: isCallablePlatformModel(model),
    }]
  }))
  const grants = new Map()
  for (const key of keys) {
    for (const grant of Array.isArray(key?.grants) ? key.grants : []) {
      if (grant?.status !== 'active' || !grant?.modelId) continue
      const row = grants.get(grant.modelId) || {
        ...(modelMap.get(grant.modelId) || { modelId: grant.modelId, modelName: '模型信息不可用', modelCode: grant.modelId, available: false }),
        keyCount: 0,
        activeKeyCount: 0,
      }
      row.keyCount += 1
      if (row.available && key.status === 'active') row.activeKeyCount += 1
      grants.set(grant.modelId, row)
    }
  }
  return [...grants.values()].sort((left, right) => left.modelCode.localeCompare(right.modelCode, 'zh-CN'))
}

export function extractIssuedKey(source = {}) {
  const payload = source?.data ?? source
  const result = {
    keyId: text(payload?.keyId ?? payload?.key_id),
    keyName: text(payload?.keyName ?? payload?.key_name),
    apiKey: text(payload?.apiKey ?? payload?.api_key),
    fingerprint: text(payload?.fingerprint),
  }
  if (!result.keyId || !result.apiKey || !result.fingerprint) throw new Error('访问密钥响应无效')
  return result
}

export function applicationStatusLabel(status) {
  return { active: '启用', disabled: '停用', unknown: '未知' }[status] || '未知'
}

export function developerKeyStatusLabel(status) {
  return { active: '有效', retiring: '轮换中', revoked: '已撤销', expired: '已过期', unknown: '未知' }[status] || '未知'
}
