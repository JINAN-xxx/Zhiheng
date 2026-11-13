import { parsePoints } from '../../billing/public.ts'
import { decodeVoid, http } from '../../../shared/http.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'

export interface Application {
  ownerId: string
  applicationId: string
  applicationName: string
  status: 'active' | 'disabled'
  platformEnabled?: boolean
  version: number
  createdAt: string
  updatedAt: string
  sourceFileLimit: number
  sourceByteLimit: number
}
export interface DeveloperKey {
  keyId: string
  keyName: string
  applicationId: string
  scopes: string[]
  resourceRpmLimit: number
  maskedKey: string
  status: 'active' | 'retiring' | 'revoked' | 'expired'
  grants: { modelId: string }[]
  quota: { rpmLimit: number | null; tpmLimit: number | null; concurrencyLimit: number | null }
  monthlyPointsLimit: string | null
  expiresAt: string | null
  lastUsedAt: string | null
}
export interface IssuedKey {
  key: DeveloperKey
  apiKey: string
}
export interface ApplicationUsage {
  id: string
  modelCode: string
  operationType: string
  status: string
  usageStatus: string
  pointsEnforced: boolean
  startedAt: string
  inputTokens: number | null
  outputTokens: number | null
  searchUnits: number | null
  billableCost: string | null
  currency: string | null
}
export interface Page<T> {
  rows: T[]
  total: number
}
export interface KeyInput {
  name: string
  scopes?: string[]
  resource_rpm_limit?: number
  model_ids: string[]
  rpm_limit: number | null
  tpm_limit: number | null
  concurrency_limit: number | null
  monthly_points_limit?: string | null
  expires_at: string | null
}
const root = '/developer/applications'
const path = (id: string) => `${root}/${encodeURIComponent(id)}`
const nullableString = (value: unknown) => (value === null ? null : string(value))
export function parseApplication(value: unknown): Application {
  const data = object(value)
  if (data.status !== 'active' && data.status !== 'disabled') throw new Error('应用状态无效')
  return {
    ownerId: string(data.owner_id),
    applicationId: string(data.id),
    applicationName: string(data.name),
    status: data.status,
    platformEnabled: data.platform_enabled === undefined ? true : boolean(data.platform_enabled),
    version: number(data.version),
    createdAt: string(data.created_at),
    sourceFileLimit: number(data.source_file_limit),
    sourceByteLimit: number(data.source_byte_limit),
    updatedAt: string(data.updated_at)
  }
}
export function parseKey(value: unknown): DeveloperKey {
  const data = object(value)
  if (
    data.status !== 'active' &&
    data.status !== 'retiring' &&
    data.status !== 'revoked' &&
    data.status !== 'expired'
  )
    throw new Error('密钥状态无效')
  return {
    keyId: string(data.id),
    keyName: string(data.name),
    applicationId: string(data.application_id),
    maskedKey: string(data.prefix),
    scopes: list(string)(data.scopes),
    resourceRpmLimit: number(data.resource_rpm_limit),
    status: data.status,
    grants: list(string)(data.model_ids).map((modelId) => ({ modelId })),
    quota: {
      rpmLimit: nullableNumber(data.rpm_limit),
      tpmLimit: nullableNumber(data.tpm_limit),
      concurrencyLimit: nullableNumber(data.concurrency_limit)
    },
    monthlyPointsLimit:
      data.monthly_points_limit === undefined || data.monthly_points_limit === null
        ? null
        : parsePoints(data.monthly_points_limit),
    expiresAt: nullableString(data.expires_at),
    lastUsedAt: nullableString(data.last_used_at)
  }
}
function parseIssued(value: unknown): IssuedKey {
  const data = object(value)
  return { key: parseKey(data), apiKey: string(data.api_key) }
}
function parseUsage(value: unknown): ApplicationUsage {
  const data = object(value)
  return {
    id: string(data.id),
    modelCode: string(data.model_id),
    operationType: string(data.operation),
    status: string(data.run_status),
    usageStatus: string(data.status),
    pointsEnforced: data.points_enforced === true,
    startedAt: string(data.created_at),
    inputTokens: nullableNumber(data.input_tokens),
    outputTokens: nullableNumber(data.output_tokens),
    searchUnits: nullableNumber(data.search_units),
    billableCost: nullableString(data.billable_cost),
    currency: nullableString(data.currency)
  }
}
function page<T>(decode: (value: unknown) => T): (value: unknown) => Page<T> {
  return (value) => {
    const data = object(value)
    return { rows: list(decode)(data.items), total: number(data.total) }
  }
}
export const listApplications = (
  query: { page: number; page_size: number; search: string },
  signal: AbortSignal
) =>
  http.request(
    `${root}?${new URLSearchParams({ page: String(query.page), page_size: String(query.page_size), search: query.search })}`,
    { signal, decode: page(parseApplication) }
  )
export const getApplication = (id: string, signal: AbortSignal) =>
  http.request(path(id), { signal, decode: parseApplication })
export const createApplication = (body: { name: string }, signal: AbortSignal) =>
  http.request(root, { method: 'POST', body, signal, decode: parseApplication })
export const updateApplication = (
  id: string,
  body: { name: string; expected_version: number },
  signal: AbortSignal
) => http.request(path(id), { method: 'PATCH', body, signal, decode: parseApplication })
export const changeApplicationStatus = (
  id: string,
  action: 'enable' | 'disable',
  signal: AbortSignal
) =>
  http.request(`${path(id)}/${action}`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseApplication
  })
export const deleteApplication = (id: string, signal: AbortSignal) =>
  http.request(path(id), { method: 'DELETE', signal, decode: parseApplication })
export async function listDeveloperKeys(id: string, signal: AbortSignal): Promise<DeveloperKey[]> {
  const keys = new Map<string, DeveloperKey>()
  const pageSize = 100
  let pageNumber = 1
  let total = 0
  do {
    const result = await http.request(`${path(id)}/keys?page=${pageNumber}&page_size=${pageSize}`, {
      signal,
      decode: page(parseKey)
    })
    for (const key of result.rows) keys.set(key.keyId, key)
    total = result.total
    if (result.rows.length < pageSize) break
    pageNumber += 1
  } while ((pageNumber - 1) * pageSize < total)
  return [...keys.values()]
}
export const createDeveloperKey = (id: string, body: KeyInput, signal: AbortSignal) =>
  http.request(`${path(id)}/keys`, { method: 'POST', body, signal, decode: parseIssued })
export const rotateDeveloperKey = (appId: string, keyId: string, signal: AbortSignal) =>
  http.request(`${path(appId)}/keys/${encodeURIComponent(keyId)}/rotate`, {
    method: 'POST',
    body: { grace_period_seconds: 86400 },
    signal,
    decode: parseIssued
  })
export const revokeDeveloperKey = (appId: string, keyId: string, signal: AbortSignal) =>
  http.request(`${path(appId)}/keys/${encodeURIComponent(keyId)}/revoke`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseKey
  })
export const deleteDeveloperKey = (appId: string, keyId: string, signal: AbortSignal) =>
  http.request(`${path(appId)}/keys/${encodeURIComponent(keyId)}`, {
    method: 'DELETE',
    signal,
    decode: decodeVoid
  })
export const listApplicationUsage = (
  id: string,
  query: { page: number; page_size: number },
  signal: AbortSignal
) =>
  http.request(
    `${path(id)}/usage?${new URLSearchParams({ page: String(query.page), page_size: String(query.page_size) })}`,
    { signal, decode: page(parseUsage) }
  )

export interface ScopeDefinition {
  scope: string
  label: string
  description: string
  requiresModels: boolean
}
export interface ScopeCatalog {
  scopes: string[]
  definitions: ScopeDefinition[]
  resourceRpmMin: number
  resourceRpmMax: number
}
export function parseScopeCatalog(value: unknown): ScopeCatalog {
  const data = object(value),
    caps = object(data.caps)
  const scopes = list(string)(data.scopes)
  const definitions = list((value) => {
    const item = object(value)
    return {
      scope: string(item.scope),
      label: string(item.label),
      description: string(item.description),
      requiresModels: boolean(item.requires_models)
    }
  })(data.definitions)
  if (
    new Set(scopes).size !== scopes.length ||
    definitions.length !== scopes.length ||
    new Set(definitions.map((item) => item.scope)).size !== scopes.length ||
    definitions.some((item) => !scopes.includes(item.scope))
  )
    throw new Error('能力目录不完整，请刷新后重试')
  return {
    scopes,
    definitions,
    resourceRpmMin: number(caps.resource_rpm_min),
    resourceRpmMax: number(caps.resource_rpm_max)
  }
}
export const getScopeCatalog = (signal: AbortSignal) =>
  http.request(`${root}/scope-catalog`, { signal, decode: parseScopeCatalog })
export function keySelectionError(
  scopes: string[],
  models: string[],
  catalog: ScopeCatalog
): string {
  if (
    !scopes.length ||
    new Set(scopes).size !== scopes.length ||
    scopes.some((scope) => !catalog.scopes.includes(scope))
  )
    return '请选择有效的操作能力'
  if (
    !models.length &&
    catalog.definitions.some((item) => scopes.includes(item.scope) && item.requiresModels)
  )
    return '模型或智能体调用至少需要一个允许的模型'
  return ''
}
export function applyResourceVersion(
  application: Application,
  version: number,
  limits?: { sourceFileLimit: number; sourceByteLimit: number },
  refreshed?: Application
): Application {
  return {
    ...application,
    ...(refreshed?.applicationId === application.applicationId ? refreshed : {}),
    ...limits,
    version
  }
}
export type ResourceKind = 'file' | 'knowledge_base' | 'skill'
export type ResourceAction = 'read' | 'write' | 'query' | 'delete'
export interface ResourceCandidate {
  kind: ResourceKind
  resourceId: string
  displayName: string
  status: string
  allowedActions: ResourceAction[]
  owned: boolean
}
export interface BoundResource extends ResourceCandidate {
  actions: ResourceAction[]
  available: boolean
}
export interface ResourceAccess {
  applicationVersion: number
  sourceCount: number
  sourceBytes: number
  sourceFileLimit: number
  sourceByteLimit: number
  canBindResources: boolean
  canConfigureQuota: boolean
  items: BoundResource[]
}
function resourceKind(value: unknown): ResourceKind {
  if (value !== 'file' && value !== 'knowledge_base' && value !== 'skill')
    throw new Error('资源类型无效')
  return value
}
function resourceAction(value: unknown): ResourceAction {
  if (value !== 'read' && value !== 'write' && value !== 'query' && value !== 'delete')
    throw new Error('资源动作无效')
  return value
}
export function parseResourceCandidate(value: unknown): ResourceCandidate {
  const data = object(value)
  return {
    kind: resourceKind(data.kind),
    resourceId: string(data.resource_id),
    displayName: string(data.display_name),
    status: string(data.status),
    allowedActions: list(resourceAction)(data.allowed_actions),
    owned: boolean(data.owned)
  }
}
export function parseResourceAccess(value: unknown): ResourceAccess {
  const data = object(value)
  return {
    applicationVersion: number(data.application_version),
    sourceCount: number(data.source_count),
    sourceBytes: number(data.source_bytes),
    sourceFileLimit: number(data.source_file_limit),
    sourceByteLimit: number(data.source_byte_limit),
    canBindResources: boolean(data.can_bind_resources),
    canConfigureQuota: boolean(data.can_configure_quota),
    items: list((value) => {
      const item = object(value)
      return {
        ...parseResourceCandidate(item),
        actions: list(resourceAction)(item.actions),
        available: boolean(item.available)
      }
    })(data.items)
  }
}
export const getResourceAccess = (id: string, signal: AbortSignal) =>
  http.request(`${path(id)}/resource-access`, { signal, decode: parseResourceAccess })
export const getResourceCandidates = (id: string, kind: ResourceKind, signal: AbortSignal) =>
  http.request(`${path(id)}/resource-candidates?kind=${kind}`, {
    signal,
    decode: list(parseResourceCandidate)
  })
const resourcePath = (id: string, kind: ResourceKind, resourceId: string) =>
  `${path(id)}/resources/${kind}/${encodeURIComponent(resourceId)}`
export const bindResource = (
  id: string,
  kind: ResourceKind,
  resourceId: string,
  actions: ResourceAction[],
  version: number,
  signal: AbortSignal
) =>
  http.request(resourcePath(id, kind, resourceId), {
    method: 'PUT',
    body: { actions, expected_version: version },
    signal,
    decode: (value) => number(object(value).application_version)
  })
export const unbindResource = (
  id: string,
  kind: ResourceKind,
  resourceId: string,
  version: number,
  signal: AbortSignal
) =>
  http.request(resourcePath(id, kind, resourceId), {
    method: 'DELETE',
    body: { expected_version: version },
    signal,
    decode: (value) => number(object(value).version)
  })
export const configureResourceQuota = (
  id: string,
  fileLimit: number,
  byteLimit: number,
  version: number,
  signal: AbortSignal
) =>
  http.request(`${path(id)}/resource-quota`, {
    method: 'PATCH',
    body: { source_file_limit: fileLimit, source_byte_limit: byteLimit, expected_version: version },
    signal,
    decode: (value) => number(object(value).version)
  })
