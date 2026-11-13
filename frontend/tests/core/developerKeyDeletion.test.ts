import assert from 'node:assert/strict'
import { after, test } from 'node:test'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = () => Promise.reject(new Error('Unexpected request'))
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const api = await import('../../src/modules/developer/api/index.ts')
const { ApiError } = await import('../../src/shared/http.ts')

void test('key deletion uses Cookie DELETE with encoded identifiers and accepts an empty 204', async () => {
  assert.equal(typeof api.deleteDeveloperKey, 'function')
  let calls = 0
  const controller = new AbortController()
  fetcher = (input, init) => {
    calls += 1
    assert.equal(input, '/api/developer/applications/app%2Fone/keys/key%2Ftwo')
    assert.equal(init?.method, 'DELETE')
    assert.equal(init?.credentials, 'include')
    assert.equal(new Headers(init?.headers).get('Content-Type'), 'application/json')
    assert.equal(init?.body, undefined)
    assert.equal(init?.signal?.aborted, false)
    return Promise.resolve(new Response(null, { status: 204 }))
  }
  assert.equal(await api.deleteDeveloperKey('app/one', 'key/two', controller.signal), undefined)
  assert.equal(calls, 1)
})

void test('key deletion preserves server authorization errors without retrying', async () => {
  assert.equal(typeof api.deleteDeveloperKey, 'function')
  let calls = 0
  fetcher = () => {
    calls += 1
    return Promise.resolve(
      new Response(JSON.stringify({ error: { code: 'forbidden', message: '无权删除此密钥' } }), {
        status: 403
      })
    )
  }
  await assert.rejects(
    api.deleteDeveloperKey('app', 'key', new AbortController().signal),
    (reason: unknown) =>
      reason instanceof ApiError && reason.status === 403 && reason.message === '无权删除此密钥'
  )
  assert.equal(calls, 1)
})

void test('key deletion honors cancellation before sending and after a late 204', async () => {
  assert.equal(typeof api.deleteDeveloperKey, 'function')
  const controller = new AbortController()
  let calls = 0
  let deliver: ((response: Response) => void) | undefined
  fetcher = () => {
    calls += 1
    return new Promise<Response>((resolve) => {
      deliver = resolve
    })
  }
  const pending = api.deleteDeveloperKey('app', 'key', controller.signal)
  controller.abort()
  assert.ok(deliver)
  deliver(new Response(null, { status: 204 }))
  await assert.rejects(pending, { name: 'AbortError' })
  await assert.rejects(api.deleteDeveloperKey('app', 'key', controller.signal), {
    name: 'AbortError'
  })
  assert.equal(calls, 1)
})
