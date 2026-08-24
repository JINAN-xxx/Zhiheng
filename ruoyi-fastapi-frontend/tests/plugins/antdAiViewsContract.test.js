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
const modelFiles = [
  'plugins/ai/views/model/index.vue',
  'plugins/ai/views/model/components/PlatformModelsPanel.vue',
  'plugins/ai/views/model/components/EngineDeploymentsPanel.vue',
  'plugins/ai/views/model/components/CredentialsPanel.vue',
  'plugins/ai/views/model/components/RuntimeStatusPanel.vue',
  'plugins/ai/views/model/components/ModelOnboardingDrawer.vue',
  'plugins/ai/views/model/components/CredentialForm.vue',
  'plugins/ai/views/model/components/DeploymentForm.vue',
]
const modelSources = modelFiles.map((fileName) => readFileSync(fileName, 'utf8')).join('\n')
const applicationFiles = [
  'plugins/ai/views/application/index.vue',
  'plugins/ai/views/usage/index.vue',
]

for (const fileName of [
  'plugins/ai/views/chat/index.vue',
  'plugins/ai/views/chat/components/AiMessage.vue',
  'plugins/ai/views/chat/components/ChatSessionPanel.vue',
  ...workspaceFiles,
  ...modelFiles,
  ...applicationFiles,
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
for (const source of [chatView, chatMessage]) {
  assert.doesNotMatch(source, /#(?:2f7e78|226862|409eff)/i)
  assert.doesNotMatch(source, /box-shadow\s*:/)
}
assert.doesNotMatch(chatView, /workspace-shadow-soft/)
assert.match(chatView, /background:\s*var\(--gray-50\)/)
assert.match(chatView, /background:\s*var\(--gray-0\)/)
assert.match(chatView, /border:\s*1px solid var\(--gray-(?:150|200)\)/)
assert.match(chatView, /focus-within[\s\S]*border-color:\s*var\(--main-color\)/)
assert.doesNotMatch(chatView, /(?:background|linear)-gradient|hover[\s\S]*transform/)
assert.match(chatMessage, /reasoningContent/)
assert.match(chatMessage, /MarkdownRender/)
assert.match(chatMessage, /typing-indicator/)
assert.match(chatMessage, /var\(--gray-150\)/)
assert.match(chatMessage, /var\(--color-text(?:-secondary)?\)/)
assert.match(chatView, /message\.metrics/)
assert.match(chatView, /runState/)
assert.match(chatSessions, /<button[\s\S]*class="session-copy"[\s\S]*<WorkspaceMoreMenu/)
assert.doesNotMatch(chatSessions, /<button[^>]*class="session-item"/)
assert.match(readFileSync('src/components/Workspace/WorkspaceMoreMenu.vue', 'utf8'), /a-dropdown/)
assert.match(readFileSync('plugins/ai/views/model/components/CredentialForm.vue', 'utf8'), /SecretField/)
assert.match(readFileSync('plugins/ai/views/model/components/PlatformModelsPanel.vue', 'utf8'), /a-dropdown/)
assert.match(readFileSync('plugins/ai/views/model/components/EngineDeploymentsPanel.vue', 'utf8'), /a-dropdown/)
assert.match(readFileSync('plugins/ai/views/model/components/CredentialsPanel.vue', 'utf8'), /a-dropdown/)
assert.doesNotMatch(modelSources, /Model Service|Provider 凭据|Model ID|Model Code|Base Model|Capability/)
assert.doesNotMatch(modelSources, /Management Key|title:\s*'Alias'|title:\s*'Blocked'/)
assert.match(readFileSync('plugins/ai/views/model/components/PlatformModelsPanel.vue', 'utf8'), /mobile-model-meta/)

const applicationView = readFileSync('plugins/ai/views/application/index.vue', 'utf8')
const usageView = readFileSync('plugins/ai/views/usage/index.vue', 'utf8')
assert.match(applicationView, /a-modal/)
assert.match(applicationView, /ResourceTable/)
assert.match(applicationView, /:columns="applicationColumns"/)
assert.match(applicationView, /:rows="applications"/)
assert.match(applicationView, /can\('ai:application:key:add'\).*can\('ai:model:list'\)/s)
assert.match(applicationView, /canQueryUsage/)
assert.match(applicationView, /当前用量数据可能已过期/)
assert.match(applicationView, /:after-close="handleKeyFormClosed"/)
assert.match(applicationView, /:after-close="clearIssuedKey"/)
assert.match(applicationView, /function clearIssuedKey\(\)[\s\S]*issuedKey\.value = null/)
assert.match(applicationView, /onBeforeUnmount\(\(\) => \{[^}]*usageRequest \+= 1/)
assert.doesNotMatch(applicationView, /Developer API|API Key|Embedding|Rerank/)
assert.match(applicationView, /开发接入/)
assert.match(applicationView, /访问密钥/)
assert.match(applicationView, /class="usage-mobile-list"/)
assert.match(usageView, /ResourceTable/)
assert.match(usageView, /a-select/)
assert.match(usageView, /canQueryUsage/)
assert.match(usageView, /ai:application:list/)
assert.match(usageView, /mobileHidden: true/)
assert.doesNotMatch(usageView, /Usage Ledger|\bChat\b|\bEmbedding\b|\bRerank\b|\bToken\b|Search Unit/)
assert.match(usageView, /用量记录/)
assert.match(usageView, /class="usage-mobile-list"/)
assert.match(applicationView, /if \(!can\('ai:application:add'\)\) return/)
assert.match(applicationView, /if \(!can\('ai:application:disable'\)/)
assert.match(applicationView, /if \(!canCreateKey\.value/)
assert.match(applicationView, /if \(command === 'rotate' && !can\('ai:application:key:rotate'\)\) return/)
assert.match(applicationView, /if \(command === 'revoke' && !can\('ai:application:key:revoke'\)\) return/)

console.log('ok Ant Design AI views contract')
