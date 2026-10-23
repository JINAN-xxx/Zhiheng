import assert from 'node:assert/strict'
import * as navigation from '../../src/components/Workspace/workspaceTransforms.js'

assert.equal(typeof navigation.filterRetiredWorkspaceRoutes, 'function', 'retired entries must be removed before registering navigation')

const routes = [
  { path: '', children: [{ path: '/index', name: 'Index', meta: { title: '首页', affix: true } }] },
  { path: 'http://zhiheng.vip', meta: { title: '若依官网' } },
  { path: '/legacy', children: [{ path: 'website', meta: { link: 'https://www.ruoyi.vip/docs' } }] },
  { path: '/ai', children: [{ path: 'chat', meta: { title: '智能对话' } }] },
  { path: '/system', children: [{ path: 'user', meta: { title: '用户管理' } }] },
  { path: 'https://example.com', meta: { title: '公司官网' } },
]
const original = structuredClone(routes)
assert.deepEqual(navigation.filterRetiredWorkspaceRoutes(routes), routes.slice(3))
assert.deepEqual(navigation.filterAdminWorkspaceRoutes(routes), routes.slice(4))
assert.deepEqual(routes, original, 'filtering must not mutate permission routes')
assert.deepEqual(navigation.filterRetiredWorkspaceRoutes([
  { path: '/index/' },
  { path: '//ruoyi.vip', meta: { title: '链接' } },
  { path: '/http://zhiheng.vip', meta: { title: '链接' } },
  { path: '/system/index', meta: { title: '系统概览' } },
]), [{ path: '/system/index', meta: { title: '系统概览' } }])

const managementOnly = navigation.collectAuthorizedWorkspacePaths(navigation.filterRetiredWorkspaceRoutes(routes.slice(0, 3).concat(routes.slice(4))))
assert.equal(navigation.resolveWorkspaceLandingPath('/index', managementOnly), '/system/user')
assert.equal(navigation.resolveWorkspaceLandingPath('/index', ['/ai/model', '/system/user']), '/ai/model')
assert.equal(navigation.resolveWorkspaceLandingPath('/index', []), '/401')
