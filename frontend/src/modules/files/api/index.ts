import { decodeVoid, http } from '../../../shared/http.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'

export type FileStatus = 'active' | 'deleted' | 'purging' | 'purged'
export type StorageStatus = 'normal' | 'missing' | 'quarantined' | 'invalid' | 'unknown'
export interface ManagedFile {
  fileId: string
  originalName: string
  displayName: string
  fileSize: number
  mimeType: string
  sha256: string
  ownerUserId: string
  uploaderUserId: string
  accessType: 'private' | 'public'
  uploaderAccessEnabled: boolean
  status: FileStatus
  aclVersion: number
  version: number
  expireTime: string | null
  deletedAt: string | null
  retentionUntil: string | null
  createTime: string
  updateTime: string
  referenceCount: number
  sourceFileId: string | null
  parserVersion: string | null
  storageStatus: StorageStatus
  aclEntryCount: number
  aclNearestExpireTime: string | null
}
export interface FileStats {
  totalCount: number
  totalSize: number
  activeCount: number
  deletedCount: number
  publicCount: number
  publicSize: number
  privateSize: number
  expiredCount: number
  retentionExpiringCount: number
  aclExpiringCount: number
}
export interface FileQuery {
  pageNum: number
  pageSize: number
  originalName: string
  accessType: '' | 'private' | 'public'
  status: '' | FileStatus
  expirationStatus: '' | 'permanent' | 'valid' | 'expiring' | 'expired'
  ownerId: string
  ownerSearch: string
  uploaderSearch: string
  mime: string
  createdFrom: string
  createdTo: string
}
export interface FileUser {
  id: string
  username: string
}
export interface FileAclEntry {
  user_id: string
  effect: 'allow' | 'deny'
  expires_at: string | null
}
export interface FileAcl {
  file: ManagedFile
  entries: FileAclEntry[]
  builtinPermissions: {
    source: 'admin' | 'owner' | 'uploader'
    enabled: boolean
    denyOverridable: boolean
  }[]
}
export interface FileReference {
  id: string
  resourceType: string
  resourceId: string
  createdAt: string
  retainUntil: string | null
  version: number
}
export interface FileAudit {
  id: string
  action: string
  actorId: string | null
  createdAt: string
  details: Record<string, unknown>
}
export interface RetentionPolicy {
  resourceType: string
  retentionDays: number
  enabled: boolean
  remark: string
  version: number
  updatedAt: string
}
export interface FileJob {
  id: string
  kind: 'expiry' | 'reminders' | 'purge' | 'reconcile'
  status: 'running' | 'succeeded' | 'failed'
  startedAt: string
  finishedAt: string | null
  scanned: number
  changed: number
  errors: string[]
}
const nullableString = (value: unknown) => (value === null ? null : string(value))
export function parseFile(value: unknown): ManagedFile {
  const data = object(value)
  if (data.access !== 'private' && data.access !== 'public') throw new Error('文件访问类型无效')
  if (!['active', 'deleted', 'purging', 'purged'].includes(string(data.status)))
    throw new Error('文件状态无效')
  if (
    !['normal', 'missing', 'quarantined', 'invalid', 'unknown'].includes(
      string(data.storage_status)
    )
  )
    throw new Error('文件存储状态无效')
  return {
    fileId: string(data.id),
    originalName: string(data.original_filename),
    displayName: string(data.display_name),
    fileSize: number(data.size),
    mimeType: string(data.mime),
    sha256: string(data.sha256),
    ownerUserId: string(data.owner_id),
    uploaderUserId: string(data.uploader_id),
    accessType: data.access,
    uploaderAccessEnabled: boolean(data.uploader_access_enabled),
    status: data.status as FileStatus,
    aclVersion: number(data.acl_version),
    version: number(data.version),
    expireTime: nullableString(data.expires_at),
    deletedAt: nullableString(data.deleted_at),
    retentionUntil: nullableString(data.retention_until),
    createTime: string(data.created_at),
    updateTime: string(data.updated_at),
    referenceCount: number(data.reference_count),
    sourceFileId: nullableString(data.source_file_id),
    parserVersion: nullableString(data.parser_version),
    storageStatus: data.storage_status as StorageStatus,
    aclEntryCount: number(data.acl_entry_count),
    aclNearestExpireTime: nullableString(data.acl_nearest_expire_at)
  }
}
export const emptyQuery = (): FileQuery => ({
  pageNum: 1,
  pageSize: 20,
  originalName: '',
  accessType: '',
  status: 'active',
  expirationStatus: '',
  ownerId: '',
  ownerSearch: '',
  uploaderSearch: '',
  mime: '',
  createdFrom: '',
  createdTo: ''
})
export const emptyStats = (): FileStats => ({
  totalCount: 0,
  totalSize: 0,
  activeCount: 0,
  deletedCount: 0,
  publicCount: 0,
  publicSize: 0,
  privateSize: 0,
  expiredCount: 0,
  retentionExpiringCount: 0,
  aclExpiringCount: 0
})
function queryString(query: FileQuery): string {
  const params = new URLSearchParams({
    page: String(query.pageNum),
    page_size: String(query.pageSize)
  })
  for (const [key, value] of Object.entries({
    search: query.originalName,
    access: query.accessType,
    status: query.status || 'all',
    expiration_status: query.expirationStatus,
    owner_id: query.ownerId,
    owner_search: query.ownerSearch,
    uploader_search: query.uploaderSearch,
    mime: query.mime,
    created_from: query.createdFrom,
    created_to: query.createdTo
  }))
    if (value) params.set(key, value)
  return params.toString()
}
const path = (id: string) => `/files/${encodeURIComponent(id)}`
export const listFiles = (query: FileQuery, signal: AbortSignal) =>
  http.request(`/files?${queryString(query)}`, {
    signal,
    decode: (value) => {
      const data = object(value)
      return { rows: list(parseFile)(data.items), total: number(data.total) }
    }
  })
export const getFileStats = (query: FileQuery, signal: AbortSignal) =>
  http.request(`/files/stats?${queryString(query)}`, {
    signal,
    decode: (value): FileStats => {
      const data = object(value)
      return {
        totalCount: number(data.total_count),
        totalSize: number(data.total_bytes),
        activeCount: number(data.active_count),
        deletedCount: number(data.deleted_count),
        publicCount: number(data.public_count),
        publicSize: number(data.public_bytes),
        privateSize: number(data.private_bytes),
        expiredCount: number(data.expired_count),
        retentionExpiringCount: number(data.retention_expiring_count),
        aclExpiringCount: number(data.acl_expiring_count)
      }
    }
  })
export const getFile = (id: string, signal: AbortSignal) =>
  http.request(path(id), { signal, decode: parseFile })
export const uploadFile = (file: File, signal: AbortSignal) =>
  http.request(`/files?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    body: file,
    signal,
    decode: parseFile
  })
export async function downloadFile(id: string, signal: AbortSignal): Promise<Blob> {
  const response = await http.open(`${path(id)}/download`, { signal })
  const blob = await response.blob()
  signal.throwIfAborted()
  return blob
}
export const updateFile = (
  file: ManagedFile,
  body: { display_name?: string; expires_at?: string | null; uploader_access_enabled?: boolean },
  signal: AbortSignal
) =>
  http.request(path(file.fileId), {
    method: 'PATCH',
    body: { expected_version: file.version, ...body },
    signal,
    decode: parseFile
  })
export const changeFileState = (
  file: ManagedFile,
  action: 'delete' | 'restore' | 'purge',
  signal: AbortSignal
) =>
  http.request(`${path(file.fileId)}${action === 'delete' ? '' : `/${action}`}`, {
    method: action === 'delete' ? 'DELETE' : 'POST',
    body: { expected_version: file.version },
    signal,
    decode: parseFile
  })
export const transferFile = (
  file: ManagedFile,
  ownerId: string,
  keepUploader: boolean,
  reason: string,
  signal: AbortSignal
) =>
  http.request(`${path(file.fileId)}/transfer`, {
    method: 'POST',
    body: {
      expected_version: file.version,
      owner_id: ownerId,
      uploader_access_enabled: keepUploader,
      reason
    },
    signal,
    decode: parseFile
  })
export const findFileUsers = (search: string, signal: AbortSignal) =>
  http.request(`/files/users?${new URLSearchParams({ search })}`, {
    signal,
    decode: list((value): FileUser => {
      const data = object(value)
      return { id: string(data.id), username: string(data.username) }
    })
  })
function parseAcl(value: unknown): FileAcl {
  const data = object(value)
  return {
    file: parseFile(data.file),
    entries: list((value): FileAclEntry => {
      const item = object(value)
      if (item.effect !== 'allow' && item.effect !== 'deny') throw new Error('授权规则无效')
      return {
        user_id: string(item.user_id),
        effect: item.effect,
        expires_at: nullableString(item.expires_at)
      }
    })(data.entries),
    builtinPermissions: list((value): FileAcl['builtinPermissions'][number] => {
      const item = object(value)
      if (item.source !== 'admin' && item.source !== 'owner' && item.source !== 'uploader')
        throw new Error('权限来源无效')
      return {
        source: item.source,
        enabled: boolean(item.enabled),
        denyOverridable: boolean(item.deny_overridable)
      }
    })(data.builtin_permissions)
  }
}
export const getFileAcl = (id: string, signal: AbortSignal) =>
  http.request(`${path(id)}/acl`, { signal, decode: parseAcl })
export const saveFileAcl = (
  file: ManagedFile,
  access: 'private' | 'public',
  entries: FileAclEntry[],
  signal: AbortSignal
) =>
  http.request(`${path(file.fileId)}/acl`, {
    method: 'PATCH',
    body: { expected_version: file.aclVersion, access, entries },
    signal,
    decode: parseAcl
  })
export const getFileReferences = (id: string, signal: AbortSignal) =>
  http.request(`${path(id)}/references`, {
    signal,
    decode: list((value): FileReference => {
      const item = object(value)
      return {
        id: string(item.id),
        resourceType: string(item.resource_type),
        resourceId: string(item.resource_id),
        createdAt: string(item.created_at),
        retainUntil: nullableString(item.retain_until),
        version: number(item.version)
      }
    })
  })
function parseAudit(value: unknown): FileAudit {
  const item = object(value)
  return {
    id: string(item.id),
    action: string(item.action),
    actorId: nullableString(item.actor_id),
    createdAt: string(item.created_at),
    details: object(item.details)
  }
}
export const getFileAudit = (id: string, signal: AbortSignal) =>
  http.request(`${path(id)}/audit`, { signal, decode: list(parseAudit) })
export interface AuditQuery {
  pageNum: number
  pageSize: number
  action: string
  actorId: string
  outcome: string
  createdFrom: string
  createdTo: string
}
export const getFileAuditPage = (id: string, query: AuditQuery, signal: AbortSignal) => {
  const params = new URLSearchParams({
    page: String(query.pageNum),
    page_size: String(query.pageSize)
  })
  for (const [key, value] of Object.entries({
    action: query.action,
    actor_id: query.actorId,
    outcome: query.outcome,
    created_from: query.createdFrom,
    created_to: query.createdTo
  }))
    if (value) params.set(key, value)
  return http.request(`${path(id)}/audit/page?${params}`, {
    signal,
    decode: (value) => {
      const data = object(value)
      return { rows: list(parseAudit)(data.items), total: number(data.total) }
    }
  })
}
function parsePolicy(value: unknown): RetentionPolicy {
  const item = object(value)
  return {
    resourceType: string(item.resource_type),
    retentionDays: number(item.retention_days),
    enabled: boolean(item.enabled),
    remark: string(item.remark),
    version: number(item.version),
    updatedAt: string(item.updated_at)
  }
}
export const getRetentionPolicies = (signal: AbortSignal) =>
  http.request('/files/policies', { signal, decode: list(parsePolicy) })
export const saveRetentionPolicy = (
  policy: RetentionPolicy | null,
  body: { resource_type: string; retention_days: number; enabled: boolean; remark: string },
  signal: AbortSignal
) =>
  http.request(`/files/policies${policy ? `/${encodeURIComponent(policy.resourceType)}` : ''}`, {
    method: policy ? 'PATCH' : 'POST',
    body: policy
      ? {
          retention_days: body.retention_days,
          enabled: body.enabled,
          remark: body.remark,
          expected_version: policy.version
        }
      : body,
    signal,
    decode: parsePolicy
  })
export const deleteRetentionPolicy = (policy: RetentionPolicy, signal: AbortSignal) =>
  http.request(`/files/policies/${encodeURIComponent(policy.resourceType)}`, {
    method: 'DELETE',
    body: { expected_version: policy.version },
    signal,
    decode: decodeVoid
  })
function parseJob(value: unknown): FileJob {
  const item = object(value)
  if (
    !['expiry', 'reminders', 'purge', 'reconcile'].includes(string(item.kind)) ||
    !['running', 'succeeded', 'failed'].includes(string(item.status))
  )
    throw new Error('文件维护状态无效')
  return {
    id: string(item.id),
    kind: item.kind as FileJob['kind'],
    status: item.status as FileJob['status'],
    startedAt: string(item.started_at),
    finishedAt: nullableString(item.finished_at),
    scanned: number(item.scanned),
    changed: number(item.changed),
    errors: list(string)(item.errors)
  }
}
export const runFileJob = (kind: FileJob['kind'], signal: AbortSignal) =>
  http.request(`/files/jobs/${kind}`, { method: 'POST', body: {}, signal, decode: parseJob })
export const getFileJobs = (signal: AbortSignal) =>
  http.request('/files/jobs', { signal, decode: list(parseJob) })
export interface ReconcileIssue {
  id: string
  jobId: string
  fileId: string | null
  kind: 'missing' | 'orphan' | 'size_or_hash' | 'invalidkey'
  status: 'open' | 'resolved' | 'ignored' | 'quarantined'
  version: number
  size: number | null
  detail: string
  expectedSize: number | null
  actualSize: number | null
  createdAt: string
  updatedAt: string
}
function parseIssue(value: unknown): ReconcileIssue {
  const item = object(value)
  if (
    !['missing', 'orphan', 'size_or_hash', 'invalidkey'].includes(string(item.kind)) ||
    !['open', 'resolved', 'ignored', 'quarantined'].includes(string(item.status))
  )
    throw new Error('对账异常状态无效')
  return {
    id: string(item.id),
    jobId: string(item.job_id),
    fileId: nullableString(item.file_id),
    kind: item.kind as ReconcileIssue['kind'],
    status: item.status as ReconcileIssue['status'],
    version: number(item.version),
    size: nullableNumber(item.size),
    detail: string(item.detail),
    expectedSize: nullableNumber(item.expected_size),
    actualSize: nullableNumber(item.actual_size),
    createdAt: string(item.created_at),
    updatedAt: string(item.updated_at)
  }
}
export const getReconcileIssues = (status: ReconcileIssue['status'], signal: AbortSignal) =>
  http.request(`/files/reconcile/issues?status=${status}`, { signal, decode: list(parseIssue) })
export const resolveReconcileIssue = (
  issue: ReconcileIssue,
  action: 'ignore' | 'quarantine' | 'register' | 'repair',
  reason: string,
  originalFilename: string,
  signal: AbortSignal
) =>
  http.request(`/files/reconcile/issues/${encodeURIComponent(issue.id)}/actions`, {
    method: 'POST',
    body: {
      expected_version: issue.version,
      action,
      reason,
      ...(originalFilename ? { original_filename: originalFilename } : {})
    },
    signal,
    decode: parseIssue
  })
export const getReconcileStats = (signal: AbortSignal) =>
  http.request('/files/reconcile/stats', {
    signal,
    decode: (value) => {
      const item = object(value)
      return {
        open: number(item.open),
        resolved: number(item.resolved),
        ignored: number(item.ignored),
        quarantined: number(item.quarantined)
      }
    }
  })
export interface FileReminder {
  id: string
  fileId: string
  kind: string
  isRead: boolean
  originalName: string
  ownerId: string
  expireTime: string | null
  referenceCount: number
  readBy: string | null
  readAt: string | null
  createdAt: string
}
function parseReminder(value: unknown): FileReminder {
  const item = object(value)
  return {
    id: string(item.id),
    fileId: string(item.file_id),
    kind: string(item.kind),
    isRead: boolean(item.is_read),
    originalName: string(item.original_filename),
    ownerId: string(item.owner_id),
    expireTime: nullableString(item.expires_at),
    referenceCount: number(item.reference_count),
    readBy: nullableString(item.read_by),
    readAt: nullableString(item.read_at),
    createdAt: string(item.created_at)
  }
}
export const getFileReminders = (signal: AbortSignal) =>
  http.request('/files/reminders', { signal, decode: list(parseReminder) })
export const markFileReminderRead = (id: string, signal: AbortSignal) =>
  http.request(`/files/reminders/${encodeURIComponent(id)}/read`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseReminder
  })
export const extendFile = (
  file: ManagedFile,
  expiresAt: string,
  reason: string,
  signal: AbortSignal
) =>
  http.request(`${path(file.fileId)}/extend`, {
    method: 'POST',
    body: { expected_version: file.version, expires_at: expiresAt, reason },
    signal,
    decode: parseFile
  })
export const disposeExpiredFile = (file: ManagedFile, signal: AbortSignal) =>
  http.request(`${path(file.fileId)}/dispose-expired`, {
    method: 'POST',
    body: { expected_version: file.version },
    signal,
    decode: parseFile
  })
