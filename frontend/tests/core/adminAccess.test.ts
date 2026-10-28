import test from 'node:test'
import assert from 'node:assert/strict'
import {
  canAccessAdmin,
  hasAdminPermission,
  visibleAdminNavigation
} from '../../src/modules/admin/access.ts'

void test('admin entry requires a real system permission and preserves super administrators', () => {
  assert.equal(canAccessAdmin(null), false)
  assert.equal(canAccessAdmin({ is_admin: false }), false)
  assert.equal(
    canAccessAdmin({ is_admin: false, permissions: ['chat:read', 'system:invented:read'] }),
    false
  )
  assert.equal(canAccessAdmin({ is_admin: false, permissions: ['system:users:read'] }), true)
  assert.equal(canAccessAdmin({ is_admin: true }), true)
  assert.equal(
    hasAdminPermission({ is_admin: false, permissions: ['system:users:read'] }, 'users', 'write'),
    false
  )
})

void test('menu metadata changes labels order and visibility without granting permission', () => {
  const user = { is_admin: false, permissions: ['system:users:read', 'system:posts:read'] }
  const menus = [
    { path: '/admin/posts', name: '团队岗位', sort_order: 1, is_visible: true },
    { path: '/admin/users', name: '成员维护', sort_order: 2, is_visible: false },
    { path: '/admin/roles', name: '越权页面', sort_order: 0, is_visible: true },
    { path: 'https://evil.example', name: '外部页面', sort_order: -1, is_visible: true }
  ]
  const navigation = visibleAdminNavigation(user, menus)
  assert.deepEqual(
    navigation.map((entry) => [entry.path, entry.label]),
    [['/admin/posts', '团队岗位']]
  )
})

void test('an authoritative navigation projection cannot revive an omitted or hidden page', () => {
  assert.deepEqual(visibleAdminNavigation({ is_admin: true }, []), [])
  assert.equal(visibleAdminNavigation({ is_admin: true }).length, 12)
  assert.deepEqual(
    visibleAdminNavigation({ is_admin: false, permissions: ['system:users:read'] }, [
      { path: '/admin/roles', name: '角色', sort_order: 0, is_visible: true }
    ]),
    []
  )
})
