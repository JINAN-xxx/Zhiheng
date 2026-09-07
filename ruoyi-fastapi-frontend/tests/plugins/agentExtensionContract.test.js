import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const api = readFileSync('plugins/agent_extension/api/skill.js', 'utf8')
const view = readFileSync('plugins/agent_extension/views/skill/index.vue', 'utf8')
const upload = readFileSync('plugins/agent_extension/views/skill/components/SkillUploadModal.vue', 'utf8')
const detail = readFileSync('plugins/agent_extension/views/skill/components/SkillDetailDrawer.vue', 'utf8')

assert.match(api, /\/agent_extension\/skills/)
assert.match(api, /\/available/)
assert.match(api, /\/import\/prepare/)
assert.match(api, /\/confirm/)
assert.match(view, /<WorkspaceToolbar\s+title="技能管理"/)
assert.match(view, /can\('agent_extension:skill:add'\)/)
assert.match(view, /class="skill-grid"/)
assert.match(view, /skill\.skillName/)
assert.match(view, /skill\.description/)
assert.match(view, /skillStatusLabel\(skill\.status\)/)
assert.doesNotMatch(view, /skill\.skillSlug|skill\.version/)
assert.match(upload, /选择文件/)
assert.match(upload, /确认安装/)
assert.match(upload, /SKILL\.md/)
assert.match(detail, /agent_extension:skill:edit/)
assert.match(detail, /agent_extension:skill:remove/)
assert.match(detail, /请先停用技能再删除/)
assert.match(detail, /技能内容/)

for (const [name, source] of [['技能列表', view], ['上传技能', upload], ['技能详情', detail]]) {
  assert.doesNotMatch(source, /#[0-9a-f]{3,8}\b/i, `${name}不能定义独立颜色`)
  assert.match(source, /var\(--(?:bg-container|gray-150|color-text|main-)/, `${name}必须复用平台主题变量`)
  assert.doesNotMatch(source, /box-shadow\s*:/, `${name}不能增加卡片阴影`)
}

console.log('ok agent extension contract')
