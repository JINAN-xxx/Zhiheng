import { http } from '../../../shared/http.ts'
import { object, string, number, boolean } from '../../../shared/validation.ts'

export interface GovernedApplication {
  id: string
  name: string
  ownerId: string
  ownerEnabled: boolean
  platformEnabled: boolean
  version: number
  sourceFileLimit: number
  sourceByteLimit: number
  sourceCount: number
  sourceBytes: number
}
export interface GovernedApplicationPage {
  items: GovernedApplication[]
  total: number
}
function decode(value: unknown): GovernedApplication {
  const row = object(value)
  return {
    id: string(row.id),
    name: string(row.name),
    ownerId: string(row.owner_id),
    ownerEnabled: boolean(row.owner_enabled),
    platformEnabled: boolean(row.platform_enabled),
    version: number(row.version),
    sourceFileLimit: number(row.source_file_limit),
    sourceByteLimit: number(row.source_byte_limit),
    sourceCount: number(row.source_count),
    sourceBytes: number(row.source_bytes)
  }
}
export function listGovernedApplications(page: number, signal: AbortSignal, ownerId = '') {
  const query = new URLSearchParams({ page: String(page), page_size: '20' })
  if (ownerId) query.set('owner_id', ownerId)
  return http.request(`/admin/applications?${query}`, {
    signal,
    decode(value): GovernedApplicationPage {
      const row = object(value)
      if (!Array.isArray(row.items)) throw new Error('应用列表格式无效')
      return { items: row.items.map(decode), total: number(row.total) }
    }
  })
}
export const setApplicationPlatformState = (
  row: GovernedApplication,
  enabled: boolean,
  signal: AbortSignal
) =>
  http.request(`/admin/applications/${encodeURIComponent(row.id)}/state`, {
    method: 'PATCH',
    body: { enabled, expected_version: row.version },
    signal,
    decode
  })
export const setApplicationResourceQuota = (
  row: GovernedApplication,
  count: number,
  bytes: number,
  signal: AbortSignal
) =>
  http.request(`/admin/applications/${encodeURIComponent(row.id)}/resource-quota`, {
    method: 'PATCH',
    body: { source_file_limit: count, source_byte_limit: bytes, expected_version: row.version },
    signal,
    decode(value) {
      return number(object(value).version)
    }
  })
