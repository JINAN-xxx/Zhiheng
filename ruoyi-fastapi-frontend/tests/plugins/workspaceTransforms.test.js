import assert from 'node:assert/strict'

import {
  groupWorkspaceActions,
  resolveWorkspaceState,
  workspaceStateLabel,
  visibleWorkspaceNav,
} from '../../src/components/Workspace/workspaceTransforms.js'

assert.deepEqual(visibleWorkspaceNav({ canChat: true, canModel: false, canApi: true }), ['chat', 'api'])
assert.equal(resolveWorkspaceState({ loading: false, rows: [], error: null }).kind, 'empty')
assert.equal(resolveWorkspaceState({ loading: false, rows: [{ id: 1 }], error: new Error('timeout') }).kind, 'stale')
assert.deepEqual(groupWorkspaceActions({ mobile: true, canEdit: true, canDelete: true }), ['more'])
assert.equal(workspaceStateLabel('stale'), '数据可能已过期')
assert.equal(workspaceStateLabel('error'), '加载失败')
