import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as antIcons from '@ant-design/icons-vue'

import { convertTreeSelectionMode, normalizeStoredTreeSelection } from '../../src/utils/treeSelection.js'

const permissionSnapshot = {
  'src/views/system/user/index.vue': ['system:user:add', 'system:user:edit', 'system:user:export', 'system:user:import', 'system:user:remove', 'system:user:resetPwd'],
  'src/views/system/user/view.vue': [],
  'src/views/system/user/authRole.vue': [],
  'src/views/system/user/profile/index.vue': [],
  'src/views/system/user/profile/userInfo.vue': [],
  'src/views/system/user/profile/resetPwd.vue': [],
  'src/views/system/user/profile/userAvatar.vue': [],
  'src/views/system/role/index.vue': ['system:role:add', 'system:role:edit', 'system:role:export', 'system:role:remove'],
  'src/views/system/role/authUser.vue': ['system:role:add', 'system:role:remove'],
  'src/views/system/role/selectUser.vue': [],
  'src/views/system/menu/index.vue': ['system:menu:add', 'system:menu:edit', 'system:menu:remove', 'system:user:list'],
  'src/views/system/dept/index.vue': ['system:dept:add', 'system:dept:edit', 'system:dept:remove'],
  'src/views/system/post/index.vue': ['system:post:add', 'system:post:edit', 'system:post:export', 'system:post:remove'],
  'src/views/system/config/index.vue': ['system:config:add', 'system:config:edit', 'system:config:export', 'system:config:remove'],
  'src/views/system/dict/index.vue': ['system:dict:add', 'system:dict:edit', 'system:dict:export', 'system:dict:remove'],
  'src/views/system/dict/data.vue': ['system:dict:add', 'system:dict:edit', 'system:dict:export', 'system:dict:remove'],
  'src/views/system/dict/detail.vue': [],
  'src/views/system/notice/index.vue': ['system:notice:add', 'system:notice:edit', 'system:notice:list', 'system:notice:remove'],
  'src/views/system/notice/ReadUsers.vue': [],
  'src/views/dashboard/index.vue': [],
  'src/views/dashboard/editable-link-group.vue': [],
}

Object.assign(permissionSnapshot, {
  'src/views/system/file/index.vue': ['system:file:edit', 'system:file:list', 'system:file:purge', 'system:file:reconcile', 'system:file:remove', 'system:file:restore', 'system:file:transfer'],
  'src/views/system/file/components/FileAclDrawer.vue': [],
  'src/views/system/file/components/FileAuditDrawer.vue': [],
  'src/views/system/file/components/FileDetailDialog.vue': [],
  'src/views/system/file/components/FileReconcileDrawer.vue': [],
  'src/views/system/file/components/FileReferenceDrawer.vue': [],
  'src/views/system/file/components/FileRetentionPolicyDrawer.vue': [],
  'src/views/system/file/components/FileRetentionReminderDrawer.vue': ['system:file:edit', 'system:file:remove'],
  'src/views/system/file/components/FileSearchForm.vue': [],
  'src/views/system/file/components/FileStatistics.vue': [],
  'src/views/system/file/components/FileTable.vue': ['system:file:download', 'system:file:edit', 'system:file:purge', 'system:file:query', 'system:file:remove', 'system:file:restore', 'system:file:transfer'],
  'src/views/system/file/components/FileTransferDialog.vue': [],
  'src/views/system/plugin/index.vue': ['system:plugin:edit', 'system:plugin:export', 'system:plugin:query', 'system:plugin:remove'],
  'src/views/system/plugin/components/PluginConfigDialog.vue': ['system:plugin:edit'],
  'src/views/system/plugin/components/PluginDependencyDialog.vue': ['system:plugin:edit'],
  'src/views/system/plugin/components/PluginDetailDialog.vue': ['system:plugin:edit'],
  'src/views/system/plugin/components/PluginDiagnosticDialog.vue': [],
  'src/views/system/plugin/components/PluginPlanDialog.vue': ['system:plugin:edit'],
  'src/views/monitor/cache/index.vue': [],
  'src/views/monitor/cache/list.vue': [],
  'src/views/monitor/job/index.vue': ['monitor:job:add', 'monitor:job:changeStatus', 'monitor:job:edit', 'monitor:job:export', 'monitor:job:query', 'monitor:job:remove'],
  'src/views/monitor/job/detail.vue': [],
  'src/views/monitor/job/log.vue': ['monitor:job:export', 'monitor:job:query', 'monitor:job:remove'],
  'src/views/monitor/logininfor/index.vue': ['monitor:logininfor:export', 'monitor:logininfor:remove', 'monitor:logininfor:unlock'],
  'src/views/monitor/online/index.vue': ['monitor:online:forceLogout'],
  'src/views/monitor/operlog/index.vue': ['monitor:operlog:export', 'monitor:operlog:query', 'monitor:operlog:remove'],
  'src/views/monitor/operlog/detail.vue': [],
  'src/views/monitor/server/index.vue': [],
  'src/views/monitor/transportCrypto/index.vue': [],
})

const listViews = new Set([
  'src/views/system/user/index.vue',
  'src/views/system/user/authRole.vue',
  'src/views/system/role/index.vue',
  'src/views/system/role/authUser.vue',
  'src/views/system/role/selectUser.vue',
  'src/views/system/menu/index.vue',
  'src/views/system/dept/index.vue',
  'src/views/system/post/index.vue',
  'src/views/system/config/index.vue',
  'src/views/system/dict/index.vue',
  'src/views/system/dict/data.vue',
  'src/views/system/dict/detail.vue',
  'src/views/system/notice/index.vue',
  'src/views/system/notice/ReadUsers.vue',
  'src/views/system/file/index.vue',
  'src/views/system/plugin/index.vue',
  'src/views/monitor/cache/index.vue',
  'src/views/monitor/cache/list.vue',
  'src/views/monitor/job/index.vue',
  'src/views/monitor/job/log.vue',
  'src/views/monitor/logininfor/index.vue',
  'src/views/monitor/online/index.vue',
  'src/views/monitor/operlog/index.vue',
])
const drawerViews = new Set([
  'src/views/system/file/components/FileAclDrawer.vue',
  'src/views/system/file/components/FileAuditDrawer.vue',
  'src/views/system/file/components/FileDetailDialog.vue',
  'src/views/system/file/components/FileReconcileDrawer.vue',
  'src/views/system/file/components/FileReferenceDrawer.vue',
  'src/views/system/file/components/FileRetentionPolicyDrawer.vue',
  'src/views/system/file/components/FileRetentionReminderDrawer.vue',
  'src/views/system/file/components/FileTransferDialog.vue',
  'src/views/system/plugin/components/PluginConfigDialog.vue',
  'src/views/system/plugin/components/PluginDependencyDialog.vue',
  'src/views/system/plugin/components/PluginDetailDialog.vue',
  'src/views/system/plugin/components/PluginDiagnosticDialog.vue',
  'src/views/system/plugin/components/PluginPlanDialog.vue',
])
const mobileActionViews = new Set([
  'src/views/system/user/index.vue',
  'src/views/system/role/index.vue',
  'src/views/system/menu/index.vue',
  'src/views/system/dict/index.vue',
  'src/views/system/config/index.vue',
])
const selectableViews = [
  'src/views/system/user/index.vue',
  'src/views/system/role/index.vue',
  'src/views/system/role/authUser.vue',
  'src/views/system/role/selectUser.vue',
]

const elementOffenders = []
const structureOffenders = []
const permissionChanges = []
const mobileActionOffenders = []
const drawerOffenders = []
const antApiOffenders = []

for (const [fileName, expectedPermissions] of Object.entries(permissionSnapshot)) {
  const source = readFileSync(fileName, 'utf8')
  const permissions = [...new Set(source.match(/(?:system|monitor|tool):[A-Za-z0-9:_-]+/g) || [])].sort()
  if (JSON.stringify(permissions) !== JSON.stringify(expectedPermissions)) permissionChanges.push({ fileName, expectedPermissions, permissions })
  if (/element-plus|@element-plus\/icons-vue|<el-|--el-|\.el-/.test(source)) elementOffenders.push(fileName)
  if (listViews.has(fileName) && !/WorkspacePage|ResourceTable/.test(source)) structureOffenders.push(fileName)
  if (mobileActionViews.has(fileName) && !/WorkspaceMoreMenu/.test(source)) mobileActionOffenders.push(fileName)
  if (drawerViews.has(fileName) && !/ResourceDrawer/.test(source)) drawerOffenders.push(fileName)
  if (/<a-table-column|active-value=|inactive-value=|<a-button[^>]* icon=|<a-form[^>]*:inline/.test(source)) antApiOffenders.push(fileName)
  if (/useWindowSize\(\)/.test(source)) assert.match(source, /import \{ useWindowSize \} from '@vueuse\/core'/, `${fileName} must import useWindowSize explicitly`)
  for (const match of source.matchAll(/import \{([^}]+)\} from ['"]@ant-design\/icons-vue['"]/g)) {
    for (const imported of match[1].split(',').map((name) => name.trim()).filter(Boolean)) {
      const iconName = imported.split(/\s+as\s+/)[0]
      assert.ok(iconName in antIcons, `${fileName} imports missing Ant icon ${iconName}`)
    }
  }
}

assert.deepEqual(permissionChanges, [], 'management permission contract changed')
assert.deepEqual(elementOffenders, [], 'management views still use Element Plus')
assert.deepEqual(structureOffenders, [], 'management list views must use shared resource surfaces')
assert.deepEqual(mobileActionOffenders, [], 'representative management rows must expose mobile action menus')
assert.deepEqual(drawerOffenders, [], 'file and plugin detail surfaces must use ResourceDrawer')
assert.deepEqual(antApiOffenders, [], 'management views still use Element-shaped Ant APIs')

const userView = readFileSync('src/views/system/user/index.vue', 'utf8')
assert.match(userView, /mobileDeptOpen/)
assert.match(userView, /v-if="!isMobile"/)
assert.match(userView, /ResourceDrawer/)
assert.match(userView, /destroy-on-close :after-close="clearResetPassword"/)
assert.match(userView, /function clearResetPassword\(\)[\s\S]*resetPasswordForm\.value = ""[\s\S]*resetPasswordUser\.value = null/)

const avatarView = readFileSync('src/views/system/user/profile/userAvatar.vue', 'utf8')
assert.doesNotMatch(avatarView, /@after-close|@after-open-change/)
assert.match(avatarView, /:after-close="closeDialog"/)
assert.match(avatarView, /function editCropper\(\) \{[\s\S]*visible\.value = true/)

const dictView = readFileSync('src/views/system/dict/index.vue', 'utf8')
assert.match(dictView, /WorkspaceMoreMenu v-if="mobileRowActions\.length"/)

for (const fileName of ['src/views/system/dict/detail.vue', 'src/views/system/notice/ReadUsers.vue']) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /requestGeneration/)
  assert.match(source, /generation !== requestGeneration/)
  assert.match(source, /onBeforeUnmount\(\(\) => \{ requestGeneration \+= 1 \}\)/)
}

for (const fileName of selectableViews) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /#headerCell/)
  assert.match(source, /toggleAll/)
}

const permissionTree = [{ id: 1, children: [{ id: 2 }, { id: 3 }] }]
assert.deepEqual(normalizeStoredTreeSelection(permissionTree, [1, 2], true), {
  checkedKeys: [2],
  halfCheckedKeys: [1],
})
assert.deepEqual(normalizeStoredTreeSelection(permissionTree, [1, 2], false), {
  checkedKeys: [1, 2],
  halfCheckedKeys: [],
})
assert.deepEqual(convertTreeSelectionMode(permissionTree, [1], [], true), {
  checkedKeys: [2, 3],
  halfCheckedKeys: [1],
})
assert.deepEqual(convertTreeSelectionMode(permissionTree, [2], [], true), {
  checkedKeys: [2],
  halfCheckedKeys: [1],
})
assert.deepEqual(convertTreeSelectionMode(permissionTree, [2], [1], false), {
  checkedKeys: [2, 1],
  halfCheckedKeys: [],
})
const roleView = readFileSync('src/views/system/role/index.vue', 'utf8')
assert.match(roleView, /function handleCheckedTreeConnect[\s\S]*convertTreeSelectionMode/)

for (const fileName of ['src/views/system/dict/data.vue', 'src/views/system/notice/index.vue']) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /Object\.keys\(form\)\.forEach\(\(key\) => delete form\[key\]\)/)
}
assert.match(readFileSync('src/views/monitor/job/index.vue', 'utf8'), /Object\.keys\(form\)\.forEach\(\(key\)\s*=>\s*delete form\[key\]\)/)

const fileAclView = readFileSync('src/views/system/file/components/FileAclDrawer.vue', 'utf8')
assert.match(fileAclView, /requestGeneration/)
assert.match(fileAclView, /isCurrentRequest\(generation, requestTarget\)/)
assert.match(fileAclView, /onBeforeUnmount\(\(\) => \{ requestGeneration \+= 1 \}\)/)
assert.match(readFileSync('src/views/system/file/components/FileAuditDrawer.vue', 'utf8'), /row-key="auditId"/)
assert.match(readFileSync('src/views/system/file/components/FileDetailDialog.vue', 'utf8'), /<a-descriptions[\s\S]*bordered/)
for (const fileName of ['src/views/system/file/components/FileAuditDrawer.vue', 'src/views/system/file/components/FileReferenceDrawer.vue']) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /requestGeneration/)
  assert.match(source, /generation !== requestGeneration/)
  assert.match(source, /onBeforeUnmount\(\(\) => \{ requestGeneration \+= 1 \}\)/)
}

const pluginIndexView = readFileSync('src/views/system/plugin/index.vue', 'utf8')
assert.match(pluginIndexView, /configGeneration/)
assert.match(pluginIndexView, /dependencyGeneration/)
assert.match(pluginIndexView, /generation !== configGeneration/)
assert.match(pluginIndexView, /generation !== dependencyGeneration/)
assert.match(pluginIndexView, /diagnosticGeneration/)
assert.match(pluginIndexView, /generation !== diagnosticGeneration/)
assert.match(readFileSync('src/views/system/plugin/components/PluginPlanDialog.vue', 'utf8'), /@update:checked="emit\('update:continueOnError', \$event\)"/)

const transportCryptoView = readFileSync('src/views/monitor/transportCrypto/index.vue', 'utf8')
assert.doesNotMatch(transportCryptoView, /active-text=|inactive-text=|inline-prompt/)
assert.match(transportCryptoView, /checked-children="自动刷新"/)
assert.match(transportCryptoView, /monitorGeneration/)
assert.match(transportCryptoView, /generation !== monitorGeneration/)

console.log(`ok Ant Design admin views contract (${Object.keys(permissionSnapshot).length} files)`)
