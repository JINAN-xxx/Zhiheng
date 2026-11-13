import { grantedPermissions } from './permissionFixture.ts'
import test from 'node:test'
import assert from 'node:assert/strict'
import { visibleAdminGroups, visibleAdminNavigation } from '../../src/modules/admin/access.ts'

void test('admin navigation consolidates all existing areas into five functional groups', () => {
  const user = { is_admin: true }
  const groups = visibleAdminGroups(user)
  assert.deepEqual(
    groups.map((group) => group.label),
    ['用户管理', '角色权限', '通知公告', '存储管理', '系统运维']
  )
  assert.equal(groups.flatMap((group) => group.entries).length, visibleAdminNavigation(user).length)
  assert.equal(groups.find((group) => group.id === 'notices')?.path, '/admin/notices')
  assert.equal(groups.find((group) => group.id === 'operations')?.path, '/admin/monitor')
})

void test('a delegated department reader lands on departments without seeing user accounts', () => {
  const groups = visibleAdminGroups({
    is_admin: false,
    permissions: grantedPermissions('system:departments:read')
  })
  assert.equal(groups.length, 1)
  assert.equal(groups[0]?.path, '/admin/departments')
  assert.deepEqual(
    groups[0]?.entries.map((entry) => entry.area),
    ['departments']
  )
})

void test('retired post grants cannot add or reorder active organization pages', () => {
  const groups = visibleAdminGroups({
    is_admin: false,
    permissions: grantedPermissions(
      'system:posts:read',
      'system:departments:read',
      'system:users:read'
    )
  })
  assert.deepEqual(
    groups[0]?.entries.map((entry) => entry.path),
    ['/admin/users', '/admin/departments']
  )
  assert.deepEqual(
    visibleAdminGroups({
      is_admin: false,
      permissions: grantedPermissions(
        'system:posts:read',
        'system:posts:write',
        'system:dictionaries:read'
      )
    }),
    []
  )
})

void test('configuration-only readers keep their notice setting entry without notice authority', () => {
  const groups = visibleAdminGroups({
    is_admin: false,
    permissions: grantedPermissions('system:configs:read')
  })
  assert.equal(groups[0]?.label, '通知公告')
  assert.equal(groups[0]?.path, '/admin/configs')
  assert.deepEqual(
    groups[0]?.entries.map((entry) => entry.area),
    ['configs']
  )
})

void test('application governance is a super-admin metadata entry, never delegated by unrelated roles', () => {
  const privileged = visibleAdminNavigation({ is_admin: true })
  assert.equal(privileged.find((item) => item.path === '/admin/applications')?.area, 'applications')
  for (const permissions of [
    ['system:users:read'],
    ['system:users:write'],
    ['ai:credits:write'],
    ['system:applications:read']
  ]) {
    assert.equal(
      visibleAdminNavigation({ is_admin: false, permissions }).some(
        (item) => item.path === '/admin/applications'
      ),
      false
    )
  }
})
