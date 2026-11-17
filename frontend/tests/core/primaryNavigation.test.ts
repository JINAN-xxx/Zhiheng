import assert from 'node:assert/strict'
import test from 'node:test'
import { primaryNavigation } from '../../src/app/navigation.ts'
import { platformModules } from '../../src/shared/platformAccess.ts'

void test('navigation preserves product grouping, names and order from canonical modules', () => {
  const groups = primaryNavigation({ is_admin: true })
  assert.deepEqual(
    groups.map((group) => [group.title, group.items.map((item) => item.label)]),
    [
      ['应用', ['对话体验', '智能助手', '工作空间']],
      ['资源', ['模型目录', '凭据管理', '知识库', '技能']],
      ['开发与用量', ['开发接入', '用量与调用']]
    ]
  )
  for (const group of groups)
    for (const item of group.items)
      assert.equal(
        item,
        platformModules.find((module) => module.id === item.id)
      )
})
void test('empty groups and revoked module gates disappear without affecting other links', () => {
  const user = {
    is_admin: false,
    permissions: [
      'platform:chat:enabled',
      'platform:chat:read',
      'platform:models:enabled',
      'platform:models:read'
    ]
  }
  assert.deepEqual(
    primaryNavigation(user).flatMap((group) => group.items.map((item) => item.path)),
    ['/chat', '/models']
  )
  user.permissions = user.permissions.filter((code) => code !== 'platform:chat:enabled')
  assert.deepEqual(
    primaryNavigation(user).map((group) => group.id),
    ['resources']
  )
  assert.deepEqual(primaryNavigation({ is_admin: false, permissions: [] }), [])
  assert.deepEqual(primaryNavigation(null), [])
})
