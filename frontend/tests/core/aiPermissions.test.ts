import test from 'node:test'
import assert from 'node:assert/strict'
import * as identity from '../../src/modules/identity/public.ts'
import { hasAdminPermission } from '../../src/modules/admin/access.ts'

void test('AI capabilities require an exact granted code, with an administrator bypass', () => {
  assert.equal(typeof identity.hasAnyPermission, 'function')
  const { hasAnyPermission } = identity
  assert.equal(hasAnyPermission(null, 'ai:models:write'), false)
  assert.equal(hasAnyPermission({ is_admin: false }, 'ai:models:write'), false)
  assert.equal(hasAnyPermission({ is_admin: true }, 'ai:models:write'), true)
  assert.equal(
    hasAnyPermission({ is_admin: false, permissions: ['ai:models:read'] }, 'ai:models:write'),
    false
  )
  assert.equal(
    hasAnyPermission(
      { is_admin: false, permissions: ['ai:models:write'] },
      'ai:models:read',
      'ai:models:write'
    ),
    true
  )
  assert.equal(
    hasAnyPermission(
      { is_admin: false, permissions: ['ai:models:*', 'system:models:write'] },
      'ai:models:write'
    ),
    false
  )
})

void test('AI capabilities do not imply system administration permissions', () => {
  assert.equal(
    hasAdminPermission({ is_admin: false, permissions: ['ai:models:write'] }, 'roles', 'write'),
    false
  )
})

void test('storage governance does not grant control of another users file', async () => {
  const access = await import('../../src/modules/files/access.ts').catch(() => null)
  assert.ok(access, 'file management must preserve owner access')
  const file = { ownerUserId: 'owner' }
  assert.equal(access.canManageFile(null, file), false)
  assert.equal(
    access.canManageFile(
      { id: 'reader', is_admin: false, permissions: ['ai:storage:write'] },
      file
    ),
    false
  )
  assert.equal(access.canManageFile({ id: 'owner', is_admin: false }, file), true)
  assert.equal(access.canManageFile({ id: 'admin', is_admin: true }, file), false)
})
