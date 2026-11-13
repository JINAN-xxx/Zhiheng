import { grantedPermissions } from './permissionFixture.ts'
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  firstModulePath,
  hasModule,
  hasCapability,
  moduleForPath,
  platformModules
} from '../../src/shared/platformAccess.ts'

void test('module grants are explicit and an empty role gets no platform access', () => {
  const empty = { is_admin: false, permissions: grantedPermissions() }
  assert.equal(firstModulePath(empty), '/access')
  assert.ok(platformModules.every((module) => !hasModule(empty, module.id)))
  const chatOnly = { is_admin: false, permissions: grantedPermissions('platform:chat:access') }
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
      permissions: grantedPermissions('platform:knowledge:access')
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
    permissions: grantedPermissions('platform:knowledge:manage', 'platform:agents:manage')
  }
  assert.equal(hasModule(maintainer, 'knowledge'), true)
  assert.equal(hasCapability(maintainer, 'knowledge', 'manage'), true)
  assert.equal(hasCapability(maintainer, 'knowledge'), false)
  assert.equal(hasCapability(maintainer, 'knowledge', 'share'), false)
  assert.equal(hasCapability(maintainer, 'agents', 'publish'), false)
  assert.equal(
    hasModule({ is_admin: false, permissions: grantedPermissions('ai:models:write') }, 'models'),
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
