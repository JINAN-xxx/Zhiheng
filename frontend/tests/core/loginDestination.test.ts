import assert from 'node:assert/strict'
import { test } from 'node:test'
import { loginDestination } from '../../src/modules/identity/navigation.ts'
void test('post-login invitation return stays inside the application', () => {
  assert.equal(loginDestination('/knowledge/join'), '/knowledge/join')
  assert.equal(
    loginDestination('/knowledge/base/documents?from=invite'),
    '/knowledge/base/documents?from=invite'
  )
  for (const path of [
    undefined,
    [],
    'https://outside.invalid',
    '//outside.invalid',
    '/\\outside.invalid',
    '/login',
    '/login/',
    '/chat\nnext'
  ])
    assert.equal(loginDestination(path), '/chat')
})
