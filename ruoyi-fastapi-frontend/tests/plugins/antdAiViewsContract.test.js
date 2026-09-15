import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { beginSend } from '../../plugins/ai/utils/chatTransforms.js'

function assertNoDecorativeEffects(source, label) {
  assert.doesNotMatch(source, /\btransform\s*:/i, `${label} must not add transforms`)
  assert.doesNotMatch(source, /(?:linear|radial|conic)-gradient\s*\(/i, `${label} must not add gradients`)
}

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
  'plugins/ai/views/application/components/ApplicationOverviewPanel.vue',
  'plugins/ai/views/application/components/ApplicationKeysPanel.vue',
  'plugins/ai/views/application/components/ApplicationModelsPanel.vue',
  'plugins/ai/views/application/components/ApplicationUsagePanel.vue',
  'plugins/ai/views/application/components/ApplicationApiDocs.vue',
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
}
assert.doesNotMatch(chatMessage, /box-shadow\s*:/)
assert.doesNotMatch(chatView, /workspace-shadow-soft/)
assert.match(chatView, /background:\s*var\(--gray-(?:10|50)\)/)
assert.match(chatView, /background:\s*var\(--bg-container\)/)
assert.match(chatView, /border:\s*1px solid var\(--gray-(?:150|200)\)/)
assert.match(chatView, /focus-within[\s\S]*border-color:\s*var\(--main-(?:color|400)\)/)
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
assert.doesNotMatch(readFileSync('plugins/ai/views/model/components/PlatformModelsPanel.vue', 'utf8'), /复制模型编号|模型编号|复制绑定别名|绑定别名/)
assert.doesNotMatch(readFileSync('plugins/ai/views/model/components/EngineDeploymentsPanel.vue', 'utf8'), /title: '部署编号'|title: '调用别名'|column\.key === 'deploymentId'|column\.key === 'alias'/)
assert.match(readFileSync('plugins/ai/views/model/components/PlatformModelsPanel.vue', 'utf8'), /title: '类型'[\s\S]*align: 'center'[\s\S]*title: '绑定状态'[\s\S]*align: 'center'[\s\S]*title: '发布状态'[\s\S]*align: 'center'/)
assert.match(readFileSync('plugins/ai/views/model/components/EngineDeploymentsPanel.vue', 'utf8'), /title: '模型'[\s\S]*align: 'left'/)

for (const fileName of modelFiles) {
  const source = readFileSync(fileName, 'utf8')
  assert.doesNotMatch(source, /#(?:2f7e78|226862|409eff)/i, `${fileName} must not use legacy brand colors`)
  assert.doesNotMatch(source, /box-shadow\s*:/, `${fileName} must not add panel shadows`)
  assert.match(source, /var\(--(?:bg-(?:container|elevated)|gray-(?:10|25|50|150)|main-(?:50|700)|color-(?:text(?:-secondary|-tertiary)?|success-(?:50|700)|warning-(?:50|700|900)|error-(?:50|700)))\)/, `${fileName} must use Yuxi surface tokens`)
}

const modelIndex = readFileSync('plugins/ai/views/model/index.vue', 'utf8')
assert.match(modelIndex, /v-if="can\('ai:engine:credential:list'\)" key="credentials" tab="凭据管理"/)
assert.doesNotMatch(modelIndex, /key="deployments"|key="status"/)
assert.doesNotMatch(modelIndex, /\.model-workspace\s*\{[^}]*border:\s*1px solid var\(--gray-150\)/)
assert.doesNotMatch(modelIndex, /\.model-workspace\s*\{[^}]*border-radius:/)
assert.match(modelIndex, /model-onboarding-drawer \.ant-drawer-content\)[\s\S]*?background:\s*var\(--bg-elevated\)/)
assert.match(modelIndex, /<PlatformModelsPanel ref="platformPanel"/)

for (const fileName of [
  'plugins/ai/views/model/components/PlatformModelsPanel.vue',
  'plugins/ai/views/model/components/EngineDeploymentsPanel.vue',
  'plugins/ai/views/model/components/CredentialsPanel.vue',
]) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /(?:\.model-table|:deep\(\.ant-table)/, `${fileName} must style its resource table`)
  assert.match(source, /var\(--gray-150\)/, `${fileName} must use a Yuxi table border`)
  assert.match(source, /var\(--gray-(?:10|25)\)/, `${fileName} must use a Yuxi table header surface`)
}

const runtimeStatus = readFileSync('plugins/ai/views/model/components/RuntimeStatusPanel.vue', 'utf8')
assert.match(runtimeStatus, /var\(--bg-container\)/)
assert.match(runtimeStatus, /var\(--gray-150\)/)
const platformModelsPanel = readFileSync('plugins/ai/views/model/components/PlatformModelsPanel.vue', 'utf8')
const onboardingDrawer = readFileSync('plugins/ai/views/model/components/ModelOnboardingDrawer.vue', 'utf8')
assert.doesNotMatch(platformModelsPanel, /title:\s*['"]能力['"]|title="能力"/)
assert.doesNotMatch(onboardingDrawer, /label="能力"/)
assert.doesNotMatch(onboardingDrawer, /<span>调用别名<\/span>/)
assert.doesNotMatch(readFileSync('plugins/ai/views/model/components/DeploymentForm.vue', 'utf8'), /<a-form-item v-if="!quick" label="路由别名"/)
assert.match(onboardingDrawer, /capabilities:\s*defaultModelCapabilities\(catalogForm\.modelType\)/)
assert.match(onboardingDrawer, /var\(--bg-container\)/)
assert.match(onboardingDrawer, /var\(--gray-150\)/)
for (const fileName of [
  'plugins/ai/views/model/components/CredentialForm.vue',
  'plugins/ai/views/model/components/DeploymentForm.vue',
]) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /var\(--(?:gray-150|color-text-secondary)\)/, `${fileName} must use Yuxi form tokens`)
  assert.match(source, /<a-form[^>]*:class="\{ 'is-quick': quick \}"/, `${fileName} must mark quick forms for sticky actions`)
  assert.match(source, /\.engine-form\.is-quick \.form-actions\s*\{[\s\S]*position:\s*sticky[\s\S]*bottom:\s*0[\s\S]*z-index:\s*\d+[\s\S]*background:\s*var\(--bg-container\)[\s\S]*border-top:\s*1px solid var\(--gray-150\)[\s\S]*padding:\s*12px 0/, `${fileName} must keep quick actions visible`)
  assert.match(source, /@media \(max-width: 640px\)[\s\S]*\.form-actions[^\n]*\{[\s\S]*flex-wrap:\s*wrap/, `${fileName} must keep mobile actions from overlapping`)
}
assert.match(onboardingDrawer, /\.step-actions\s*\{[\s\S]*position:\s*sticky[\s\S]*bottom:\s*0[\s\S]*z-index:\s*\d+[\s\S]*background:\s*var\(--bg-container\)[\s\S]*border-top:\s*1px solid var\(--gray-150\)/)
assert.match(onboardingDrawer, /@media \(max-width: 640px\)[\s\S]*\.step-actions[^\n]*\{[\s\S]*flex-wrap:\s*wrap/)

const applicationView = readFileSync('plugins/ai/views/application/index.vue', 'utf8')
const applicationApiDocs = readFileSync('plugins/ai/views/application/components/ApplicationApiDocs.vue', 'utf8')
const applicationApi = readFileSync('plugins/ai/api/application.js', 'utf8')
const requestSource = readFileSync('src/utils/request.js', 'utf8')
const applicationUtils = readFileSync('plugins/ai/utils/application.js', 'utf8')
const applicationKeysPanel = readFileSync('plugins/ai/views/application/components/ApplicationKeysPanel.vue', 'utf8')
const applicationModelsPanel = readFileSync('plugins/ai/views/application/components/ApplicationModelsPanel.vue', 'utf8')
const applicationUsagePanel = readFileSync('plugins/ai/views/application/components/ApplicationUsagePanel.vue', 'utf8')
const usageView = readFileSync('plugins/ai/views/usage/index.vue', 'utf8')
assert.throws(
  () => assertNoDecorativeEffects('.bad { transform: translateY(1px); }', 'mutation transform'),
  /mutation transform.*transform/i,
)
assert.throws(
  () => assertNoDecorativeEffects('.bad { background: radial-gradient(circle, red, blue); }', 'mutation gradient'),
  /mutation gradient.*gradient/i,
)
assert.match(applicationView, /a-modal/)
assert.match(applicationView, /ResourceTable/)
assert.match(applicationView, /:columns="applicationColumns"/)
assert.match(applicationView, /:rows="applications"/)
assert.match(applicationView, /can\('ai:application:key:add'\).*can\('ai:model:list'\)/s)
assert.match(applicationView, /canQueryUsage/)
assert.match(applicationUsagePanel, /当前用量数据可能已过期/)
assert.match(applicationView, /:after-close="handleKeyFormClosed"/)
assert.match(applicationView, /:after-close="clearIssuedKey"/)
assert.match(applicationView, /function clearIssuedKey\(\)[\s\S]*issuedKey\.value = null/)
assert.match(applicationView, /onBeforeUnmount\(\(\) => \{[^}]*usageRequest \+= 1/)
assert.doesNotMatch(applicationView, /Developer API|API Key|Embedding|Rerank/)
assert.match(applicationView, /开发接入/)
assert.match(applicationView, /class="application-list-view"/)
assert.match(applicationView, /class="application-context-bar"/)
assert.match(applicationView, /class="application-back"/)
assert.match(applicationView, /showApplicationList/)
assert.match(applicationView, /backToApplicationList/)
assert.match(applicationView, /route\.query\.view === ['"]docs['"]|showDocs/)
assert.match(applicationView, /API 说明/)
assert.match(applicationView, /ReloadOutlined[\s\S]*API 说明[\s\S]*新建应用/)
assert.match(applicationView, /view:\s*['"]docs['"]|view=docs/)
assert.match(applicationView, /delete.*view|view.*undefined|closeApiDocs/s)
assert.match(applicationApiDocs, /GET \/v1\/models/)
assert.match(applicationApiDocs, /POST \/v1\/chat\/completions/)
assert.match(applicationApiDocs, /POST \/v1\/embeddings/)
assert.match(applicationApiDocs, /POST \/v1\/rerank/)
assert.match(applicationApiDocs, /复制调用地址/)
assert.match(applicationApiDocs, /api-overview/)
assert.match(applicationApiDocs, /api-auth/)
assert.match(applicationApiDocs, /api-streaming/)
assert.match(applicationApiDocs, /api-errors/)
assert.match(applicationApiDocs, /location\.hash/)
assert.match(applicationApiDocs, /hashchange/)
assert.match(applicationApiDocs, /aria-current/)
assert.match(applicationApiDocs, /onBeforeUnmount[\s\S]*removeEventListener\(['"]hashchange['"]/)
assert.match(applicationApiDocs, /aria-label="请求字段"|:aria-label="`\$\{item\.title\}请求字段`"/)
assert.match(applicationApiDocs, /aria-label="普通响应字段"/)
assert.match(applicationApiDocs, /aria-label="流式响应字段"/)
assert.match(applicationApiDocs, /aria-label="错误码"/)
assert.match(applicationApiDocs, /feedback\.msgError/)
assert.match(applicationView, /resolveDocsExitAction/)
assert.match(applicationView, /await loadApplications/)
assert.match(applicationView, /await loadActiveTabData/)
assert.match(applicationApiDocs, /普通响应字段/)
assert.match(applicationApiDocs, /流式响应字段/)
assert.match(applicationApiDocs, /presence_penalty|apiDocs/)
assert.doesNotMatch(applicationApiDocs, /issuedKey|localStorage|sk-zh|sk-live/)
assertNoDecorativeEffects(applicationApiDocs, 'plugins/ai/views/application/components/ApplicationApiDocs.vue')
assert.doesNotMatch(applicationApiDocs, /box-shadow\s*:/)
assert.match(applicationView, /访问密钥/)
assert.match(applicationView, /概览/)
assert.match(applicationView, /模型授权/)
assert.match(applicationView, /调用记录/)
assert.match(applicationView, /route\.query\.tab/)
assert.match(applicationView, /router\.replace\([\s\S]*applicationId[\s\S]*tab/)
assert.match(applicationView, /can\('ai:application:enable'\)/)
assert.match(applicationView, /can\('ai:application:delete'\)/)
assert.match(applicationView, /selectedApplication\.status === 'disabled'/)
assert.match(applicationView, /重新启用/)
assert.match(applicationView, /删除应用/)
assert.match(applicationView, /仅停用应用可以删除/)
assert.match(applicationApi, /export function enableApplication\(applicationId\)[\s\S]*buildApplicationPath\(applicationId, 'enable'\)[\s\S]*method: 'post'/)
assert.match(applicationApi, /export function deleteApplication\(applicationId\)[\s\S]*buildApplicationPath\(applicationId\)[\s\S]*method: 'delete'/)
assert.match(applicationApi, /export function getApplication\(applicationId\)[\s\S]*buildApplicationPath\(applicationId\)[\s\S]*method: 'get'/)
assert.match(applicationApi, /acceptNotFound: true[\s\S]*status === 404/)
assert.match(requestSource, /res\.status === 404 && res\.config\?\.acceptNotFound === true/)
assert.match(applicationUtils, /export function aggregateGrantedModels/)
assert.match(applicationView, /resolvePreferredApplication\(page\.rows, preferredId, getApplication\)/)
assert.match(applicationView, /await resolvePreferredApplication\(page\.rows, preferredId, getApplication\)[\s\S]*if \(requestId !== applicationRequest\) return[\s\S]*await selectApplication\(next, true, 'load'\)/)
assert.match(applicationView, /watch\(\(\) => route\.query\.applicationId,[\s\S]*if \(applicationId === selectedApplication\.value\?\.applicationId\) return[\s\S]*await loadApplications\(applicationId \|\| null\)/)
assert.match(applicationKeysPanel, /访问密钥/)
assert.match(applicationModelsPanel, /aggregateGrantedModels/)
assert.match(applicationModelsPanel, /record\.available \? '可用' : '不可用'/)
assert.match(applicationModelsPanel, /缺少访问密钥或模型目录权限/)
assert.match(applicationUsagePanel, /class="usage-mobile-list"/)
assert.match(usageView, /ResourceTable/)
assert.match(usageView, /a-select/)
assert.match(usageView, /canQueryUsage/)
assert.match(usageView, /ai:application:list/)
assert.match(usageView, /mobileHidden: true/)
assert.doesNotMatch(usageView, /Usage Ledger|\bChat\b|\bEmbedding\b|\bRerank\b|\bToken\b|Search Unit/)
assert.match(usageView, /用量记录/)
assert.match(usageView, /class="usage-mobile-list"/)
assert.match(applicationView, /if \(!application && !can\('ai:application:add'\)\) return/)
assert.match(applicationView, /if \(!can\('ai:application:disable'\)/)
assert.match(applicationView, /if \(!can\('ai:application:enable'\)/)
assert.match(applicationView, /if \(!can\('ai:application:delete'\)/)
assert.match(applicationView, /if \(!canCreateKey\.value/)
assert.match(applicationView, /if \(command === 'rotate' && !can\('ai:application:key:rotate'\)\) return/)
assert.match(applicationView, /if \(command === 'revoke' && !can\('ai:application:key:revoke'\)\) return/)
assert.match(applicationView, /applicationTabResources\(activeTab\.value\)/)
assert.match(applicationView, /collectAllPages\(/)
assert.match(applicationView, /normalizeApplicationModelPage\(await listPlatformModels\(\{ pageNum, pageSize \}\)\)/)
assert.match(applicationView, /if \(source === 'manual'\) \{[\s\S]*applicationRequest \+= 1[\s\S]*loading\.value = false/)
assert.match(applicationView, /models\.value\.filter\(isCallablePlatformModel\)/)
assert.match(applicationView, /canViewApplicationModels\(canListKeys\.value, can\('ai:model:list'\)\)/)
assert.match(applicationView, /<ApplicationKeysPanel[\s\S]*?:can-list="canListKeys"[\s\S]*?\/>/)
assert.match(applicationView, /<ApplicationModelsPanel[\s\S]*?:can-view="canViewModelAuthorizations"[\s\S]*?\/>/)
assert.match(applicationView, /async function loadModelAuthorizations\(\) \{[\s\S]*if \(!canViewModelAuthorizations\.value\) return/)
assert.match(applicationView, /await selectApplication\(next, true, 'load'\)/)
assert.match(applicationView, /keyForm\.expiresAt\?\.toDate\?\.\(\)/)
assert.match(platformModelsPanel, /canRemove && \['draft', 'disabled'\]\.includes\(row\.lifecycleStatus\)/)
assert.match(platformModelsPanel, /确认删除模型/)

for (const [fileName, source] of [
  ['plugins/ai/views/application/index.vue', applicationView],
  ['plugins/ai/views/usage/index.vue', usageView],
]) {
  assert.doesNotMatch(source, /#(?:2f7e78|226862|409eff)/i, `${fileName} must not use legacy brand colors`)
  assert.doesNotMatch(source, /box-shadow\s*:/, `${fileName} must not add panel shadows`)
  assertNoDecorativeEffects(source, fileName)
  assert.doesNotMatch(source, /--workspace-(?:surface|border(?:-strong)?|main-soft|warning(?:-soft)?)/, `${fileName} must use direct Yuxi visual tokens`)
  assert.match(source, /var\(--bg-container\)/, `${fileName} must use the Yuxi container surface`)
  assert.match(source, /var\(--gray-150\)/, `${fileName} must use the Yuxi border token`)
}

assert.match(applicationView, /\.developer-grid > :deep\(\.workspace-panel\)\s*\{[\s\S]*border:\s*1px solid var\(--gray-150\)[\s\S]*background:\s*var\(--bg-container\)/)
assert.match(applicationView, /\.application-detail\s*\{[\s\S]*border:\s*1px solid var\(--gray-150\)[\s\S]*background:\s*var\(--bg-container\)/)
assert.match(applicationView, /\.application-row\.is-active\s*\{[\s\S]*background:\s*var\(--main-50\)[\s\S]*color:\s*var\(--main-700\)/)
assert.match(applicationView, /\.application-row:hover\s*\{[\s\S]*background:\s*var\(--gray-50\)[\s\S]*color:\s*var\(--color-text\)/)
assert.match(applicationView, /\.application-row\.is-active:hover\s*\{[\s\S]*background:\s*var\(--main-30\)[\s\S]*color:\s*var\(--main-700\)/)
assert.match(applicationView, /\.stale-notice\s*\{[\s\S]*color:\s*var\(--color-warning-900\)[\s\S]*background:\s*var\(--color-warning-50\)/)
assert.match(applicationKeysPanel, /\.key-row code[\s\S]*font-family:\s*var\(--workspace-font-mono\)/)
assert.match(applicationUsagePanel, /\.usage-mobile-item\s*\{[\s\S]*border:\s*1px solid var\(--gray-150\)[\s\S]*border-radius:\s*8px[\s\S]*background:\s*var\(--bg-container\)/)

assert.match(usageView, /\.usage-controls\s*\{[\s\S]*border:\s*1px solid var\(--gray-150\)[\s\S]*background:\s*var\(--bg-container\)/)
assert.match(usageView, /\.usage-table-wrap\s*\{[\s\S]*border:\s*1px solid var\(--gray-150\)[\s\S]*background:\s*var\(--bg-container\)/)
assert.match(usageView, /\.usage-table-wrap :deep\(\.ant-table-thead > tr > th\)\s*\{[\s\S]*background:\s*var\(--gray-10\)[\s\S]*border-bottom-color:\s*var\(--gray-150\)/)
assert.match(usageView, /\.usage-stale\s*\{[\s\S]*color:\s*var\(--color-warning-900\)[\s\S]*background:\s*var\(--color-warning-50\)/)
assert.match(usageView, /\.usage-mobile-item\s*\{[\s\S]*border:\s*1px solid var\(--gray-150\)[\s\S]*border-radius:\s*8px[\s\S]*background:\s*var\(--bg-container\)/)

console.log('ok Ant Design AI views contract')
