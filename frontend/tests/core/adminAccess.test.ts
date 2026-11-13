import { grantedPermissions } from './permissionFixture.ts'
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
    canAccessAdmin({
      is_admin: false,
      permissions: grantedPermissions('chat:read', 'system:invented:read')
    }),
    false
  )
  assert.equal(
    canAccessAdmin({ is_admin: false, permissions: grantedPermissions('system:users:read') }),
    true
  )
  assert.equal(canAccessAdmin({ is_admin: true }), true)
  assert.equal(
    hasAdminPermission(
      { is_admin: false, permissions: grantedPermissions('system:users:read') },
      'users',
      'write'
    ),
    false
  )
})

void test('fixed navigation follows real permissions and keeps its built-in names and order', () => {
  const user = {
    is_admin: false,
    permissions: grantedPermissions('system:users:read', 'system:departments:read')
  }
  assert.deepEqual(
    visibleAdminNavigation(user).map((entry) => [entry.path, entry.label]),
    [
      ['/admin/users', '用户管理'],
      ['/admin/departments', '部门管理']
    ]
  )
  assert.equal(
    visibleAdminNavigation(user).some((entry) => entry.area === 'roles'),
    false
  )
})

void test('retired feature permissions do not grant a console entry and retired pages are absent', () => {
  assert.equal(
    canAccessAdmin({
      is_admin: false,
      permissions: grantedPermissions(
        'system:menus:read',
        'system:menus:write',
        'system:dictionaries:read',
        'system:dictionaries:write',
        'system:posts:read',
        'system:posts:write'
      )
    }),
    false
  )
  const paths = visibleAdminNavigation({ is_admin: true }).map((entry) => entry.path)
  assert.equal(paths.length, 11)
  assert.equal(paths.includes('/admin/menus'), false)
  assert.equal(paths.includes('/admin/dictionaries'), false)
  assert.equal(paths.includes('/admin/posts'), false)
  assert.equal(paths.includes('/admin/knowledge-settings'), false)
})

void test('storage operators enter the admin console through their existing AI permission', () => {
  const user = { is_admin: false, permissions: grantedPermissions('ai:storage:read') }
  assert.equal(canAccessAdmin(user), true)
  assert.equal(hasAdminPermission(user, 'storage'), true)
  assert.deepEqual(
    visibleAdminNavigation(user).map((entry) => entry.path),
    ['/admin/storage']
  )
  assert.equal(hasAdminPermission(user, 'configs'), false)
})
