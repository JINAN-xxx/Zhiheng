import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  canRunAction,
  resolveAsyncState,
  resolvePlatformIcon,
  sanitizeSecretDefault,
} from '../../src/components/Antd/componentContracts.js'

const componentFiles = [
  'WorkspacePage.vue',
  'AsyncState.vue',
  'ResourceTable.vue',
  'ResourceDrawer.vue',
  'PermissionAction.vue',
  'SecretField.vue',
  'PlatformIcon.vue',
]

assert.deepEqual(resolveAsyncState({ loading: true, rows: [] }), { kind: 'loading' })
assert.deepEqual(resolveAsyncState({ error: 'x', rows: [] }), { kind: 'error' })
assert.deepEqual(resolveAsyncState({ error: 'x', rows: [1] }), { kind: 'stale' })
assert.deepEqual(resolveAsyncState({ loading: false, rows: [] }), { kind: 'empty' })
assert.deepEqual(resolveAsyncState({ loading: false, rows: [1] }), { kind: 'ready' })

assert.equal(canRunAction(['ai:model:edit'], ['ai:model:edit'], false), true)
assert.equal(canRunAction(['*:*:*'], ['ai:model:edit'], false), true)
assert.equal(canRunAction(['ai:model:list'], ['ai:model:edit'], false), false)
assert.equal(canRunAction(['ai:model:edit'], ['ai:model:edit'], true), false)

assert.equal(sanitizeSecretDefault('api_key', 'secret'), null)
assert.equal(sanitizeSecretDefault('vertex_credentials', 'secret'), null)
assert.equal(sanitizeSecretDefault('region', 'us-east-1'), 'us-east-1')
assert.equal(resolvePlatformIcon('unknown-icon'), 'QuestionOutlined')
assert.equal(resolvePlatformIcon('user'), 'UserOutlined')

for (const fileName of componentFiles) {
  const source = readFileSync(`src/components/Antd/${fileName}`, 'utf8')
  assert.doesNotMatch(source, /element-plus|@element-plus\/icons-vue|<el-/)
}

assert.match(readFileSync('src/components/Antd/AsyncState.vue', 'utf8'), /可能已过期/)
assert.match(readFileSync('src/components/Antd/ResourceDrawer.vue', 'utf8'), /a-drawer/)
assert.match(readFileSync('src/components/Antd/SecretField.vue', 'utf8'), /autocomplete="new-password"/)
assert.match(readFileSync('src/components/Antd/PlatformIcon.vue', 'utf8'), /QuestionOutlined/)
