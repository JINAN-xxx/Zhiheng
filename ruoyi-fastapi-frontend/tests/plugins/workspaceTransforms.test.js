import assert from 'node:assert/strict'

import {
  collectWorkspaceRoutePaths,
  collectWorkspaceLeafPaths,
  filterAdminWorkspaceRoutes,
  groupWorkspaceActions,
  normalizeWorkspaceSessions,
  resolveAiResourceGroups,
  resolveAiResourceTabs,
  resolvePaginationLayout,
  resolveWorkspaceLandingPath,
  resolveWorkspaceMode,
  resolveWorkspaceState,
  shouldApplyGeneration,
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
assert.equal(resolveWorkspaceLandingPath('/', ['/ai/model', '/system/user']), '/ai/model')
assert.equal(resolveWorkspaceLandingPath('/index', ['/system/user']), '/system/user')
assert.equal(resolveWorkspaceLandingPath('/ai/model', ['/ai/chat']), '/ai/model')
assert.equal(resolveWorkspaceMode('/ai/chat'), 'ai')
assert.equal(resolveWorkspaceMode('/ai'), 'ai')
assert.equal(resolveWorkspaceMode('/system/user'), 'admin')
assert.deepEqual(
  resolveAiResourceTabs(new Set(['ai:model:list', 'ai:application:list'])).map((item) => item.key),
  ['model', 'application', 'usage'],
)
assert.deepEqual(
  resolveAiResourceTabs(new Set(['*:*:*'])).map((item) => item.path),
  ['/ai/model', '/ai/application', '/ai/usage'],
)
assert.deepEqual(
  resolveAiResourceGroups(new Set(['*:*:*'])),
  [{
    key: 'model-center',
    label: '模型中心',
    path: '/ai/model',
    children: [
      { key: 'model', label: '模型目录', path: '/ai/model', permission: 'ai:model:list' },
      { key: 'application', label: '开发接入', path: '/ai/application', permission: 'ai:application:list' },
      { key: 'usage', label: '用量记录', path: '/ai/usage', permission: 'ai:application:list' },
    ],
  }],
)
assert.deepEqual(
  resolveAiResourceGroups(new Set(['ai:model:list']))[0]?.children.map((item) => item.key),
  ['model'],
)
assert.deepEqual(
  resolveAiResourceGroups(new Set(['ai:application:list']))[0]?.children.map((item) => item.key),
  ['application', 'usage'],
)
assert.deepEqual(resolveAiResourceGroups(new Set()), [])
assert.deepEqual(
  filterAdminWorkspaceRoutes([
    { path: '/ai', children: [{ path: 'chat' }] },
    { path: '/system', children: [{ path: 'user' }] },
  ]),
  [{ path: '/system', children: [{ path: 'user' }] }],
)
assert.deepEqual(
  normalizeWorkspaceSessions({
    data: {
      rows: [
        { sessionId: 's-1', title: '模型排查', updatedAt: '2026-08-20T08:00:00Z' },
        { session_id: 's-2', name: '接口设计', updated_at: '2026-08-20T07:00:00Z' },
        null,
      ],
    },
  }),
  [
    { sessionId: 's-1', title: '模型排查', updatedAt: '2026-08-20T08:00:00Z' },
    { sessionId: 's-2', title: '接口设计', updatedAt: '2026-08-20T07:00:00Z' },
  ],
)
assert.equal(shouldApplyGeneration(2, 2), true)
assert.equal(shouldApplyGeneration(1, 2), false)
assert.deepEqual(collectWorkspaceRoutePaths([
  { path: '/ai', children: [{ path: 'chat' }, { path: '/absolute' }] },
]), ['/ai', '/ai/chat', '/absolute'])
assert.deepEqual(collectWorkspaceLeafPaths([
  { path: '/system', children: [{ path: 'user' }, { path: 'role' }] },
]), ['/system/user', '/system/role'])
assert.deepEqual(collectWorkspaceLeafPaths([
  { path: '/system', children: [{ path: 'user', hidden: true }, { path: 'role' }] },
]), ['/system/role'])
assert.deepEqual(collectWorkspaceLeafPaths([
  { path: '/ai', children: [{ path: 'chat' }] },
  { path: '/system', children: [{ path: 'overview' }] },
]), ['/ai/chat', '/system/overview'])
assert.deepEqual(collectWorkspaceLeafPaths([
  { path: '', children: [{ path: '/index' }] },
  { path: 'https://example.com/admin' },
  { path: '//example.com/admin' },
  { path: '' },
]), ['/index'])
assert.deepEqual(
  collectWorkspaceLeafPaths(filterAdminWorkspaceRoutes([
    { path: '/ai', children: [{ path: 'chat' }] },
    { path: '/system', children: [{ path: 'overview' }] },
  ])),
  ['/system/overview'],
)
assert.deepEqual(normalizeChatSessions({ data: { rows: [{ id: 's-1', name: '项目讨论', updated_at: '2026-08-18T10:00:00Z' }] } }), [
  { id: 's-1', name: '项目讨论', updated_at: '2026-08-18T10:00:00Z', sessionId: 's-1', title: '项目讨论', updatedAt: '2026-08-18T10:00:00Z' },
])
assert.equal(resolveInitialSessionId([{ sessionId: 's-1' }]), 's-1')
assert.equal(resolveInitialSessionId([{ sessionId: 's-1' }], 'missing'), 's-1')
assert.equal(chatMessageLabel('assistant'), '智衡')
assert.equal(resolvePaginationLayout(390, 'total, sizes, prev, pager, next, jumper'), 'prev, pager, next')
assert.equal(resolvePaginationLayout(1440, 'total, sizes, prev, pager, next, jumper'), 'total, sizes, prev, pager, next, jumper')
