import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const modelView = readFileSync('plugins/ai/views/model/index.vue', 'utf8')
const applicationView = readFileSync('plugins/ai/views/application/index.vue', 'utf8')
const usageView = readFileSync('plugins/ai/views/usage/index.vue', 'utf8')
const resourceTable = readFileSync('src/components/Antd/ResourceTable.vue', 'utf8')
const workspaceToolbar = readFileSync('src/components/Workspace/WorkspaceToolbar.vue', 'utf8')
const modelPanels = [
  'PlatformModelsPanel.vue',
  'EngineDeploymentsPanel.vue',
  'CredentialsPanel.vue',
  'RuntimeStatusPanel.vue',
].map((name) => readFileSync(`plugins/ai/views/model/components/${name}`, 'utf8'))

assert.match(modelView, /<WorkspaceToolbar\s+title="模型中心"\s+subtitle="管理模型绑定、发布状态和计费规则"/)
assert.match(usageView, /<WorkspaceToolbar\s+title="用量记录"\s+subtitle="查看调用结果、消耗与费用"/)
assert.match(applicationView, /import WorkspaceToolbar from ['"]@\/components\/Workspace\/WorkspaceToolbar\.vue['"]/) 
assert.match(applicationView, /<WorkspaceToolbar\s+title="开发接入"\s+subtitle="管理开发接入应用、模型授权与访问密钥"[\s\S]*?<\/WorkspaceToolbar>/)
assert.doesNotMatch(applicationView, /<header class="developer-header">/)
assert.doesNotMatch(applicationView, /\.developer-header\s*\{/)

assert.match(resourceTable, /:deep\(\.ant-table\)\s*\{[^}]*font-size:\s*14px/)
assert.match(resourceTable, /:deep\(\.ant-table-thead[^}]*\{[^}]*font-size:\s*13px/)
assert.match(resourceTable, /:deep\(\.ant-table-tbody[^}]*\{[^}]*min-height:\s*48px|:deep\(\.ant-table-tbody[^}]*\{[^}]*line-height:\s*48px/)
assert.match(resourceTable, /:deep\(\.ant-table-tbody > tr\)\s*\{[^}]*height:\s*48px/)
assert.match(resourceTable, /:deep\(\.ant-table-tbody > tr > td\)\s*\{[^}]*min-height:\s*48px/)
for (const modelPanel of modelPanels) {
  assert.doesNotMatch(modelPanel, /\.model-table[^}]*\.ant-table-thead[^}]*height:\s*44px/)
  assert.doesNotMatch(modelPanel, /\.model-table[^}]*\.ant-table-tbody[^}]*height:\s*46px/)
}
assert.match(applicationView, /\.application-row\s*\{[^}]*min-height:\s*48px[^}]*padding:\s*10px\s+16px/s)
assert.doesNotMatch(modelView, /\.workspace-tabs :deep\(\.ant-table\)\s*\{\s*font-size:\s*12px/)
assert.doesNotMatch(modelView, /\.workspace-tabs :deep\(\.ant-table-thead[^}]*font-size:\s*12px/)
assert.match(modelView, /\.workspace-tabs :deep\(\.ant-table\)\s*\{\s*font-size:\s*14px/)

assert.match(workspaceToolbar, /font-size:\s*22px/)
assert.match(workspaceToolbar, /__subtitle[\s\S]*font-size:\s*14px/)
assert.match(modelView, /margin-top:\s*24px/)
assert.match(usageView, /margin:\s*24px\s+0/)

console.log('ok subpage layout contract')
