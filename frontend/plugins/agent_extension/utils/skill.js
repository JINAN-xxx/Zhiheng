const read = (source, camel, snake, fallback = '') => source?.[camel] ?? source?.[snake] ?? fallback

export function normalizeSkill(source = {}) {
  return {
    skillId: read(source, 'skillId', 'skill_id'),
    skillSlug: read(source, 'skillSlug', 'skill_slug'),
    skillName: read(source, 'skillName', 'skill_name'),
    description: read(source, 'description', 'description'),
    version: read(source, 'version', 'version'),
    status: read(source, 'status', 'status') === 'enabled' ? 'enabled' : 'disabled',
    sourceType: read(source, 'sourceType', 'source_type', 'upload'),
    lockVersion: Number(read(source, 'lockVersion', 'lock_version', 1)) || 1,
    contentHash: read(source, 'contentHash', 'content_hash'),
    metadataJson: read(source, 'metadataJson', 'metadata_json', {}),
    createdBy: read(source, 'createdBy', 'created_by'),
    createdAt: read(source, 'createdAt', 'created_at'),
    updatedBy: read(source, 'updatedBy', 'updated_by'),
    updatedAt: read(source, 'updatedAt', 'updated_at'),
  }
}

export function normalizeSkillPage(response = {}) {
  const source = response?.data?.rows ? response.data : response
  const rows = Array.isArray(source?.rows) ? source.rows.map(normalizeSkill) : []
  return { rows, total: Number(source?.total) || rows.length }
}

export function normalizeAvailableSkills(response = {}) {
  const rows = Array.isArray(response?.data) ? response.data : Array.isArray(response) ? response : []
  return rows.map((item) => ({
    skillSlug: read(item, 'skillSlug', 'skill_slug'),
    skillName: read(item, 'skillName', 'skill_name'),
    description: read(item, 'description', 'description'),
  }))
}

export const skillStatusLabel = (status) => status === 'enabled' ? '已启用' : '已停用'

export function hasSkillPermission(permissions, permission) {
  return Array.isArray(permissions) && (permissions.includes('*:*:*') || permissions.includes(permission))
}

export function formatSkillBytes(value) {
  const bytes = Number(value) || 0
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function shouldOpenSkillPicker(value) {
  return /(^|\s)@$/.test(String(value || ''))
}

export function removeTerminalSkillTrigger(value) {
  return String(value || '').replace(/(^|\s)@$/, '$1')
}
