import { grantedPermissions } from './permissionFixture.ts'
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  firstModulePath,
  hasModule,
  hasPermission,
  hasCapability,
  moduleForPath,
  platformModules
} from '../../src/shared/platformAccess.ts'

void test('module grants are explicit and an empty role gets no platform access', () => {
  const empty = { is_admin: false, permissions: grantedPermissions() }
  assert.equal(firstModulePath(empty), '/access')
  assert.ok(platformModules.every((module) => !hasModule(empty, module.id)))
  const chatOnly = { is_admin: false, permissions: grantedPermissions('platform:chat:use') }
  assert.equal(hasModule(chatOnly, 'chat'), true)
  assert.equal(hasModule(chatOnly, 'knowledge'), false)
  assert.equal(hasModule(chatOnly, 'models'), false)
  assert.equal(firstModulePath(chatOnly), '/chat')
})
void test('deep routes, new modules and administrator recovery use the same decisions', () => {
  assert.equal(moduleForPath('/agents/a/chat'), 'agents')
  assert.equal(moduleForPath('/knowledge/settings'), 'knowledge')
  assert.equal(moduleForPath('/agent_extension/agent'), 'agents')
  for (const [path, module] of [
    ['/ai/chat', 'chat'],
    ['/ai/chat/work', 'work'],
    ['/ai/model', 'models'],
    ['/ai/application', 'developer'],
    ['/ai/usage', 'usage'],
    ['/agent_extension/skill', 'skills'],
    ['/agent_extension/agent/a/chat', 'agents']
  ])
    assert.equal(moduleForPath(path!), module)
  for (const path of ['/files', '/knowledge/storage', '/system/file'])
    assert.equal(moduleForPath(path), null)
  assert.equal(moduleForPath('/profile'), null)
  assert.equal(moduleForPath('/access'), null)
  assert.equal(
    firstModulePath({
      is_admin: false,
      permissions: grantedPermissions('platform:knowledge:query')
    }),
    '/knowledge'
  )
  assert.equal(
    firstModulePath({ is_admin: false, permissions: grantedPermissions('system:roles:read') }),
    '/admin'
  )
  assert.ok(platformModules.every((module) => hasModule({ is_admin: true }, module.id)))
})

void test('maintenance grants navigation but never imply use, sharing or credentials', () => {
  const maintainer = {
    is_admin: false,
    permissions: grantedPermissions(
      'platform:knowledge:update',
      'platform:knowledge:import',
      'platform:agents:debug'
    )
  }
  assert.equal(hasModule(maintainer, 'knowledge'), true)
  assert.equal(hasCapability(maintainer, 'knowledge', 'update'), true)
  assert.equal(hasCapability(maintainer, 'knowledge', 'query'), false)
  assert.equal(hasCapability(maintainer, 'knowledge', 'share'), false)
  assert.equal(hasCapability(maintainer, 'agents', 'publish'), false)
  assert.equal(
    hasModule(
      { is_admin: false, permissions: grantedPermissions('platform:models:update') },
      'models'
    ),
    true
  )
})

void test('stored child operations are inactive when their module is off', () => {
  const disabled = {
    is_admin: false,
    permissions: ['platform:knowledge:read', 'platform:knowledge:create']
  }
  assert.equal(hasModule(disabled, 'knowledge'), false)
  assert.equal(hasCapability(disabled, 'knowledge', 'create'), false)
})

void test('read prerequisites and exact operations remain independent', () => {
  const user = {
    is_admin: false,
    permissions: ['platform:agents:enabled', 'platform:agents:create']
  }
  assert.equal(hasCapability(user, 'agents', 'create'), false)
  user.permissions.push('platform:agents:read')
  assert.equal(hasCapability(user, 'agents', 'create'), true)
  assert.equal(hasCapability(user, 'agents', 'update'), false)
  assert.equal(hasCapability(user, 'agents', 'use'), false)
  assert.equal(hasPermission(user, 'platform:agents:manage'), false)
  assert.equal(hasPermission(user, 'platform:agents:access'), false)
})

void test('stored historical aggregates never grant current operations or module entry', () => {
  const user = {
    is_admin: false,
    permissions: [
      'platform:knowledge:manage',
      'platform:chat:access',
      'ai:models:write',
      'ai:skills:write'
    ]
  }
  assert.equal(firstModulePath(user), '/access')
  assert.equal(hasCapability(user, 'knowledge', 'update'), false)
  assert.equal(hasCapability(user, 'chat', 'use'), false)
  assert.equal(hasCapability(user, 'models', 'update'), false)
  assert.equal(hasCapability(user, 'skills', 'create'), false)
})

void test('test grants reject aggregates and unknown operations instead of expanding them', () => {
  for (const code of [
    'platform:chat:access',
    'platform:knowledge:manage',
    'ai:models:write',
    'system:invented:read'
  ])
    assert.throws(() => grantedPermissions(code), /canonical permission/)
  assert.deepEqual(grantedPermissions('platform:chat:enabled'), ['platform:chat:enabled'])
})
