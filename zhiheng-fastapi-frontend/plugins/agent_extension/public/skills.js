import request from '@/utils/request'

const BASE_URL = '/agent_extension/skills'

export function listAvailableSkills() {
  return request({ url: `${BASE_URL}/available`, method: 'get' })
}

export { normalizeAvailableSkills, shouldOpenSkillPicker, removeTerminalSkillTrigger } from '../utils/skill.js'
