import assert from 'node:assert/strict'

import {
  formatSkillBytes,
  hasSkillPermission,
  normalizeAvailableSkills,
  normalizeSkill,
  normalizeSkillPage,
  skillStatusLabel,
} from '../../plugins/agent_extension/utils/skill.js'

assert.deepEqual(
  normalizeSkillPage({ rows: [{ skill_id: 'skl-1', skill_name: '总结' }], total: '1' }),
  {
    rows: [
      {
        skillId: 'skl-1',
        skillSlug: '',
        skillName: '总结',
        description: '',
        version: '',
        status: 'disabled',
        sourceType: 'upload',
        lockVersion: 1,
        contentHash: '',
        metadataJson: {},
        createdBy: '',
        createdAt: '',
        updatedBy: '',
        updatedAt: '',
      },
    ],
    total: 1,
  },
)
assert.equal(normalizeSkill({ skillId: 'a', status: 'enabled' }).status, 'enabled')
assert.deepEqual(normalizeAvailableSkills({ data: [{ skillSlug: 'report', skillName: '报告' }] }), [
  { skillSlug: 'report', skillName: '报告', description: '' },
])
assert.equal(skillStatusLabel('enabled'), '已启用')
assert.equal(skillStatusLabel('disabled'), '已停用')
assert.equal(formatSkillBytes(1024), '1 KB')
assert.equal(hasSkillPermission(['agent_extension:skill:add'], 'agent_extension:skill:add'), true)
assert.equal(hasSkillPermission(['*:*:*'], 'agent_extension:skill:remove'), true)

console.log('ok skill transforms')
