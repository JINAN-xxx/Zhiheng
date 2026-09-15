import assert from 'node:assert/strict'

import {
  collectWorkspaceRoutePaths,
  collectWorkspaceLeafPaths,
  collectAuthorizedWorkspacePaths,
  filterAdminWorkspaceRoutes,
  getActiveAiResourceChildPath,
  groupWorkspaceActions,
  isAiResourceGroupActive,
  normalizeWorkspaceSessions,
  resolveAiResourceGroups,
  resolveAiResourceTabs,
  resolveAgentExtensionGroups,
  resolveAuthorizedWorkspacePath,
  resolvePaginationLayout,
  resolveWorkspaceLandingPath,
  resolveWorkspaceMode,
  resolveConversationWorkspaceMode,
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
assert.equal(resolveWorkspaceMode('/ai/work'), 'ai')
assert.equal(resolveWorkspaceMode('/ai/admin'), 'ai')
assert.equal(resolveConversationWorkspaceMode('/ai/work'), 'work')
assert.equal(resolveConversationWorkspaceMode('/ai/chat/work'), 'work')
assert.equal(resolveConversationWorkspaceMode('/ai/chat/work/task'), 'work')
assert.equal(resolveConversationWorkspaceMode('/ai/chat/work-other'), 'chat')
assert.equal(resolveConversationWorkspaceMode('/ai/chat'), 'chat')
assert.equal(resolveWorkspaceMode('/ai'), 'ai')
assert.equal(resolveWorkspaceMode('/extensions/skill'), 'ai')
assert.equal(resolveWorkspaceMode('/system/user'), 'admin')
assert.deepEqual(
  resolveAiResourceTabs(new Set(['ai:model:list', 'ai:application:list'])).map((item) => item.key),
  ['model', 'application'],
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
      { key: 'usage', label: '用量记录', path: '/ai/usage', permission: 'ai:application:query' },
    ],
  }],
)
assert.deepEqual(
  resolveAiResourceGroups(new Set(['ai:model:list']))[0]?.children.map((item) => item.key),
  ['model'],
)
assert.deepEqual(
  resolveAiResourceGroups(new Set(['ai:application:list'])),
  [{
    key: 'model-center',
    label: '模型中心',
    path: '/ai/application',
    children: [
      { key: 'application', label: '开发接入', path: '/ai/application', permission: 'ai:application:list' },
    ],
  }],
)
assert.deepEqual(
  resolveAiResourceGroups(new Set(['ai:application:query'])),
  [{
    key: 'model-center',
    label: '模型中心',
    path: '/ai/usage',
    children: [
      { key: 'usage', label: '用量记录', path: '/ai/usage', permission: 'ai:application:query' },
    ],
  }],
)
assert.deepEqual(resolveAiResourceGroups(new Set()), [])
assert.deepEqual(resolveAgentExtensionGroups(new Set(['agent_extension:skill:list'])), [
  {
    key: 'agent-extension',
    label: '智能体扩展',
    path: '/extensions/skill',
    children: [
      {
        key: 'skill',
        label: '技能管理',
        path: '/extensions/skill',
        permission: 'agent_extension:skill:list',
      },
    ],
  },
])
assert.equal(isAiResourceGroupActive({ children: [{ path: '/ai/application' }] }, '/ai/application'), true)
assert.equal(isAiResourceGroupActive({ children: [{ path: '/ai/application' }] }, '/ai/application/detail'), true)
assert.equal(isAiResourceGroupActive({ children: [{ path: '/ai/application' }] }, '/ai/application-old'), false)
assert.equal(
  getActiveAiResourceChildPath({ children: [{ path: '/ai/application' }, { path: '/ai/application/detail' }] }, '/ai/application/detail'),
  '/ai/application/detail',
)
assert.equal(
  getActiveAiResourceChildPath({ children: [{ path: '/ai/application' }] }, '/ai/application/detail'),
  '/ai/application',
)
assert.equal(
  getActiveAiResourceChildPath({ children: [{ path: '/ai/application' }] }, '/ai/application-old'),
  null,
)
assert.equal(resolveAuthorizedWorkspacePath('/ai/application', ['/ai/chat']), '/ai/chat')
assert.equal(resolveAuthorizedWorkspacePath('/ai/work', ['/ai/chat', '/ai/work']), '/ai/work')
assert.equal(resolveAuthorizedWorkspacePath('/ai/chat/work', ['/ai/chat']), '/ai/chat')
assert.equal(resolveAuthorizedWorkspacePath('/ai/chat/work/task', ['/ai/chat']), '/ai/chat')
assert.equal(resolveAuthorizedWorkspacePath('/ai/chat/work', ['/ai/chat/work']), '/ai/chat/work')
assert.equal(resolveWorkspaceLandingPath('/index', ['/ai/chat/work']), '/ai/chat/work')
assert.equal(resolveWorkspaceLandingPath('/index', ['/ai/chat/work', '/ai/chat']), '/ai/chat')
assert.equal(resolveAuthorizedWorkspacePath('/ai/application/detail', ['/ai/chat']), '/ai/chat')
assert.equal(resolveAuthorizedWorkspacePath('/ai/application', ['/ai/application']), '/ai/application')
assert.equal(
  resolveAuthorizedWorkspacePath('/ai/usage', ['/ai', '/ai/application']),
  '/ai/application',
)
assert.equal(
  resolveAuthorizedWorkspacePath('/ai/application/detail', ['/ai', '/ai/application']),
  '/ai/application/detail',
)
assert.equal(resolveAuthorizedWorkspacePath('/system/user', ['/ai/chat']), '/system/user')
assert.equal(resolveAuthorizedWorkspacePath('/extensions/skill', ['/ai/chat']), '/ai/chat')
assert.deepEqual(
  filterAdminWorkspaceRoutes([
    { path: '/ai', children: [{ path: 'chat' }] },
    { path: '/extensions', children: [{ path: 'skill' }] },
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
assert.deepEqual(collectAuthorizedWorkspacePaths([
  { path: '/ai/work', hidden: true, meta: { workspaceAccess: true }, children: [{ path: '' }] },
  { path: '/system/secret', hidden: true, children: [{ path: '' }] },
  { path: '/ai', children: [{ path: 'chat' }] },
]), ['/ai/work', '/ai/chat'])
assert.equal(resolveWorkspaceLandingPath('/index', ['/ai/work', '/ai/chat']), '/ai/chat')
assert.equal(resolveWorkspaceLandingPath('/index', ['/ai/work']), '/ai/work')
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
