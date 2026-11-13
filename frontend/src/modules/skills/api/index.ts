import { http } from '../../../shared/http.ts'
import { list, number, object, string } from '../../../shared/validation.ts'
export interface Skill {
  skillId: string
  skillSlug: string
  skillName: string
  description: string
  version: string
  lockVersion: number
  status: 'enabled' | 'disabled'
  sourceType: 'builtin' | 'upload'
  revisionId: string
  contentHash: string
  fileCount: number
  totalBytes: number
}
export interface PreparedSkill {
  draftId: string
  expiresAt: string
  skillSlug: string
  skillName: string
  description: string
  version: string
  fileCount: number
  totalBytes: number
  action: 'install' | 'update' | 'restore' | 'unchanged'
  targetSkillId: string | null
  expectedVersion: number | null
  currentPackageVersion: string | null
}
function parseSkill(value: unknown): Skill {
  const data = object(value)
  if (data.status !== 'enabled' && data.status !== 'disabled') throw new Error('技能状态无效')
  if (data.source !== 'builtin' && data.source !== 'upload') throw new Error('技能来源无效')
  return {
    skillId: string(data.id),
    skillSlug: string(data.slug),
    skillName: string(data.name),
    description: string(data.description),
    version: string(data.package_version),
    lockVersion: number(data.version),
    status: data.status,
    sourceType: data.source,
    revisionId: string(data.revision_id),
    contentHash: string(data.content_hash),
    fileCount: number(data.file_count),
    totalBytes: number(data.total_bytes)
  }
}
function parsePrepared(value: unknown): PreparedSkill {
  const data = object(value)
  const action = data.action ?? 'install'
  if (!['install', 'update', 'restore', 'unchanged'].includes(string(action)))
    throw new Error('技能导入动作无效')
  return {
    draftId: string(data.draft_id),
    expiresAt: string(data.expires_at),
    skillSlug: string(data.slug),
    skillName: string(data.name),
    description: string(data.description),
    version: string(data.package_version),
    fileCount: number(data.file_count),
    totalBytes: number(data.total_bytes),
    action: action as PreparedSkill['action'],
    targetSkillId: data.target_skill_id == null ? null : string(data.target_skill_id),
    expectedVersion: data.expected_version == null ? null : number(data.expected_version),
    currentPackageVersion:
      data.current_package_version == null ? null : string(data.current_package_version)
  }
}
export interface SkillListQuery {
  keyword: string
  status: string | undefined
  pageNum: number
  pageSize: number
  source?: Skill['sourceType']
}
export const listSkills = (query: SkillListQuery, signal: AbortSignal) => {
  const params = new URLSearchParams({
    page: String(query.pageNum),
    page_size: String(query.pageSize),
    search: query.keyword
  })
  if (query.status) params.set('status', query.status)
  if (query.source) params.set('source', query.source)
  return http.request(`/skills?${params}`, {
    signal,
    decode: (value) => {
      const data = object(value)
      return { rows: list(parseSkill)(data.items), total: number(data.total) }
    }
  })
}
export const listAvailableSkills = (signal: AbortSignal) =>
  http.request('/skills/available', { signal, decode: list(parseSkill) })
const path = (id: string) => `/skills/${encodeURIComponent(id)}`
export const getSkill = (id: string, signal: AbortSignal) =>
  http.request(path(id), { signal, decode: parseSkill })
export const getSkillContent = (id: string, signal: AbortSignal) =>
  http.request(`${path(id)}/content`, { signal, decode: (value) => string(object(value).content) })
export const prepareSkillImport = (file: File, signal: AbortSignal) =>
  http.request(`/skills/import/prepare?${new URLSearchParams({ filename: file.name })}`, {
    method: 'POST',
    body: file,
    signal,
    decode: parsePrepared
  })
export const confirmSkillImport = (id: string, signal: AbortSignal) =>
  http.request(`/skills/import/${encodeURIComponent(id)}/confirm`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseSkill
  })
export const updateSkillStatus = (
  id: string,
  status: Skill['status'],
  version: number,
  signal: AbortSignal
) =>
  http.request(`${path(id)}/status`, {
    method: 'PATCH',
    body: { status, expected_version: version },
    signal,
    decode: parseSkill
  })
export const deleteSkill = (id: string, signal: AbortSignal) =>
  http.request(path(id), { method: 'DELETE', signal, decode: parseSkill })
