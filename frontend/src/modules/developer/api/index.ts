import { parsePoints } from '../../billing/presentation.ts'
import { http } from '../../../shared/http.ts'
import { list, nullableNumber, number, object, string } from '../../../shared/validation.ts'

export interface Application {
  ownerId: string
  applicationId: string
  applicationCode: string
  applicationName: string
  status: 'active' | 'disabled'
  version: number
  createdAt: string
  updatedAt: string
}
export interface DeveloperKey {
  keyId: string
  keyName: string
  applicationId: string
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
  startedAt: string
  inputTokens: number | null
  outputTokens: number | null
  searchUnits: number | null
  billableCost: string | null
  currency: string
}
export interface Page<T> {
  rows: T[]
  total: number
}
export interface KeyInput {
  name: string
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
function parseApplication(value: unknown): Application {
  const data = object(value)
  if (data.status !== 'active' && data.status !== 'disabled') throw new Error('应用状态无效')
  return {
    ownerId: string(data.owner_id),
    applicationId: string(data.id),
    applicationCode: string(data.code),
    applicationName: string(data.name),
    status: data.status,
    version: number(data.version),
    createdAt: string(data.created_at),
    updatedAt: string(data.updated_at)
  }
}
function parseKey(value: unknown): DeveloperKey {
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
    startedAt: string(data.created_at),
    inputTokens: nullableNumber(data.input_tokens),
    outputTokens: nullableNumber(data.output_tokens),
    searchUnits: nullableNumber(data.search_units),
    billableCost: nullableString(data.billable_cost),
    currency: string(data.currency)
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
export const createApplication = (body: { code: string; name: string }, signal: AbortSignal) =>
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
export const listApplicationUsage = (
  id: string,
  query: { page: number; page_size: number },
  signal: AbortSignal
) =>
  http.request(
    `${path(id)}/usage?${new URLSearchParams({ page: String(query.page), page_size: String(query.page_size) })}`,
    { signal, decode: page(parseUsage) }
  )
