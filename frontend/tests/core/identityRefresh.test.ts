import assert from 'node:assert/strict'
import test from 'node:test'

void test('identity refresh observes grants and revocations without a new login', async () => {
  const originalFetch = globalThis.fetch
  const originalNow = Date.now
  let now = 100000
  let calls = 0
  let permissions = ['ai:models:read']
  globalThis.fetch = () => {
    calls += 1
    return Promise.resolve(
      new Response(
        JSON.stringify({ id: 'identity-test', username: 'member', is_admin: false, permissions }),
        { headers: { 'Content-Type': 'application/json' } }
      )
    )
  }
  Date.now = () => now
  try {
    const { restoreIdentity, identity, clearIdentity } = await import(
      '../../src/modules/identity/public.ts'
    )
    clearIdentity()
    await restoreIdentity()
    await restoreIdentity()
    assert.equal(calls, 1)
    permissions = ['ai:models:read', 'ai:models:test']
    now += 31000
    await restoreIdentity()
    assert.deepEqual(identity.value?.permissions, permissions)
    permissions = []
    await restoreIdentity(true)
    assert.deepEqual(identity.value?.permissions, [])
    assert.equal(calls, 3)
    clearIdentity()
  } finally {
    globalThis.fetch = originalFetch
    Date.now = originalNow
  }
})
