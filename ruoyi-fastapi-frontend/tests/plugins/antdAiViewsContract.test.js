import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { beginSend } from '../../plugins/ai/utils/chatTransforms.js'

const chatView = readFileSync('plugins/ai/views/chat/index.vue', 'utf8')
const chatMessage = readFileSync('plugins/ai/views/chat/components/AiMessage.vue', 'utf8')
const chatSessions = readFileSync('plugins/ai/views/chat/components/ChatSessionPanel.vue', 'utf8')
const workspaceFiles = [
  'src/components/Workspace/WorkspaceSidebar.vue',
  'src/components/Workspace/WorkspaceMoreMenu.vue',
  'src/components/Workspace/WorkspaceState.vue',
  'src/components/Workspace/WorkspaceToolbar.vue',
]

for (const fileName of [
  'plugins/ai/views/chat/index.vue',
  'plugins/ai/views/chat/components/AiMessage.vue',
  'plugins/ai/views/chat/components/ChatSessionPanel.vue',
  ...workspaceFiles,
]) {
  const source = readFileSync(fileName, 'utf8')
  assert.doesNotMatch(source, /element-plus|@element-plus\/icons-vue|<el-|--el-/)
}

assert.deepEqual(beginSend({ runId: 'run-old', status: 'done' }), {
  runId: null,
  status: 'connecting',
})

assert.match(chatView, /a-select/)
assert.match(chatView, /a-modal/)
assert.match(chatView, /a-upload/)
assert.match(chatView, /a-textarea/)
assert.match(chatView, /class="composer-submit"/)
assert.match(chatMessage, /RightOutlined/)
assert.match(chatSessions, /<button[\s\S]*class="session-copy"[\s\S]*<WorkspaceMoreMenu/)
assert.doesNotMatch(chatSessions, /<button[^>]*class="session-item"/)
assert.match(readFileSync('src/components/Workspace/WorkspaceMoreMenu.vue', 'utf8'), /a-dropdown/)

console.log('ok Ant Design AI views contract')
