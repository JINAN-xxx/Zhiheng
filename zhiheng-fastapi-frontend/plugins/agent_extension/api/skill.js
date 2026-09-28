import request from '@/utils/request'

const BASE_URL = '/agent_extension/skills'

export function listSkills(query) {
  return request({ url: BASE_URL, method: 'get', params: query })
}

export function listAvailableSkills() {
  return request({ url: `${BASE_URL}/available`, method: 'get' })
}

export function getSkill(skillId) {
  return request({ url: `${BASE_URL}/${encodeURIComponent(skillId)}`, method: 'get' })
}

export function getSkillContent(skillId) {
  return request({ url: `${BASE_URL}/${encodeURIComponent(skillId)}/content`, method: 'get' })
}

export function prepareSkillImport(file) {
  const data = new FormData()
  data.append('file', file)
  return request({
    url: `${BASE_URL}/import/prepare`,
    method: 'post',
    data,
    headers: { 'Content-Type': 'multipart/form-data', repeatSubmit: false, encrypt: false },
  })
}

export function confirmSkillImport(draftId) {
  return request({
    url: `${BASE_URL}/import/${encodeURIComponent(draftId)}/confirm`,
    method: 'post',
    headers: { repeatSubmit: false },
  })
}

export function updateSkillStatus(skillId, data) {
  return request({ url: `${BASE_URL}/${encodeURIComponent(skillId)}/status`, method: 'put', data })
}

export function deleteSkill(skillId) {
  return request({ url: `${BASE_URL}/${encodeURIComponent(skillId)}`, method: 'delete' })
}
