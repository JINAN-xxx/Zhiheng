import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

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

for (const [fileName, expectedPermissions] of Object.entries(permissionSnapshot)) {
  const source = readFileSync(fileName, 'utf8')
  const permissions = [...new Set(source.match(/(?:system|monitor|tool):[A-Za-z0-9:_-]+/g) || [])].sort()
  if (JSON.stringify(permissions) !== JSON.stringify(expectedPermissions)) permissionChanges.push({ fileName, expectedPermissions, permissions })
  if (/element-plus|@element-plus\/icons-vue|<el-|--el-/.test(source)) elementOffenders.push(fileName)
  if (listViews.has(fileName) && !/WorkspacePage|ResourceTable/.test(source)) structureOffenders.push(fileName)
  if (mobileActionViews.has(fileName) && !/WorkspaceMoreMenu/.test(source)) mobileActionOffenders.push(fileName)
  if (/useWindowSize\(\)/.test(source)) assert.match(source, /import \{ useWindowSize \} from '@vueuse\/core'/, `${fileName} must import useWindowSize explicitly`)
}

assert.deepEqual(permissionChanges, [], 'management permission contract changed')
assert.deepEqual(elementOffenders, [], 'management views still use Element Plus')
assert.deepEqual(structureOffenders, [], 'management list views must use shared resource surfaces')
assert.deepEqual(mobileActionOffenders, [], 'representative management rows must expose mobile action menus')

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

console.log(`ok Ant Design admin views contract (${Object.keys(permissionSnapshot).length} files)`)
