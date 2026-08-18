import assert from 'node:assert/strict'

import {
  collectWorkspaceRoutePaths,
  groupWorkspaceActions,
  resolvePaginationLayout,
  resolveWorkspaceLandingPath,
  resolveWorkspaceState,
  workspaceStateLabel,
  visibleWorkspaceNav,
} from '../../src/components/Workspace/workspaceTransforms.js'
import {
  chatMessageLabel,
  normalizeChatSessions,
  resolveInitialSessionId,
} from '../../plugins/ai/utils/chatTransforms.js'

assert.deepEqual(visibleWorkspaceNav({ canChat: true, canModel: false, canApi: true }), ['chat', 'api'])
assert.equal(resolveWorkspaceState({ loading: false, rows: [], error: null }).kind, 'empty')
assert.equal(resolveWorkspaceState({ loading: false, rows: [{ id: 1 }], error: new Error('timeout') }).kind, 'stale')
assert.deepEqual(groupWorkspaceActions({ mobile: true, canEdit: true, canDelete: true }), ['more'])
assert.equal(workspaceStateLabel('stale'), '数据可能已过期')
assert.equal(workspaceStateLabel('error'), '加载失败')
assert.equal(resolveWorkspaceLandingPath('/index', ['/ai/chat']), '/ai/chat')
assert.equal(resolveWorkspaceLandingPath('/index', ['/system/user']), '/index')
assert.deepEqual(collectWorkspaceRoutePaths([
  { path: '/ai', children: [{ path: 'chat' }, { path: '/absolute' }] },
]), ['/ai', '/ai/chat', '/absolute'])
assert.deepEqual(normalizeChatSessions({ data: { rows: [{ id: 's-1', name: '项目讨论', updated_at: '2026-08-18T10:00:00Z' }] } }), [
  { id: 's-1', name: '项目讨论', updated_at: '2026-08-18T10:00:00Z', sessionId: 's-1', title: '项目讨论', updatedAt: '2026-08-18T10:00:00Z' },
])
assert.equal(resolveInitialSessionId([{ sessionId: 's-1' }]), 's-1')
assert.equal(resolveInitialSessionId([{ sessionId: 's-1' }], 'missing'), 's-1')
assert.equal(chatMessageLabel('assistant'), '智衡')
assert.equal(resolvePaginationLayout(390, 'total, sizes, prev, pager, next, jumper'), 'prev, pager, next')
assert.equal(resolvePaginationLayout(1440, 'total, sizes, prev, pager, next, jumper'), 'total, sizes, prev, pager, next, jumper')
