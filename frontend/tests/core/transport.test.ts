import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApiError, createHttpClient, RequestScope } from '../../src/shared/http.ts'
const decodeString = (value: unknown): string => {
  assert.equal(typeof value, 'string')
  return String(value)
}
void test('HTTP uses Cookie credentials, direct DTO and stable error message without retries', async () => {
  let calls = 0
  const client = createHttpClient({
    fetch: (_input, init) => {
      calls += 1
      assert.equal(init?.credentials, 'include')
      assert.equal(init?.body, '{"name":"test"}')
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'denied', message: '无权限' } }), {
          status: 403
        })
      )
    }
  })
  await assert.rejects(
    client.request('/models', { method: 'POST', body: { name: 'test' }, decode: decodeString }),
    (error: unknown) =>
      error instanceof ApiError && error.code === 'denied' && error.message === '无权限'
  )
  assert.equal(calls, 1)
})
void test('late unauthorized response cannot invalidate a newer authenticated session', async () => {
  let respond: ((value: Response) => void) | undefined
  let invalidations = 0
  const client = createHttpClient({
    fetch: () =>
      new Promise<Response>((resolve) => {
        respond = resolve
      }),
    onUnauthorized: () => {
      invalidations += 1
    }
  })
  const oldRequest = client
    .request('/auth/me', { decode: decodeString })
    .catch((error: unknown) => error)
  client.invalidate()
  assert.ok(respond)
  respond(new Response('{}', { status: 401 }))
  await oldRequest
  assert.equal(invalidations, 0)
})
void test('current unauthorized response clears identity', async () => {
  let invalidations = 0
  const client = createHttpClient({
    fetch: () => Promise.resolve(new Response('{}', { status: 401 })),
    onUnauthorized: () => {
      invalidations += 1
    }
  })
  await assert.rejects(client.request('/auth/me', { decode: decodeString }), ApiError)
  assert.equal(invalidations, 1)
})
void test('replacing request context aborts previous signal and rejects late writes', () => {
  const scope = new RequestScope()
  const previous = scope.current()
  const current = scope.replace()
  assert.equal(previous.signal.aborted, true)
  assert.equal(previous.isCurrent(), false)
  assert.equal(current.isCurrent(), true)
  scope.dispose()
  assert.equal(current.isCurrent(), false)
})

void test('session invalidation during JSON decoding rejects an otherwise successful late response', async () => {
  let deliver: ((chunk: Uint8Array) => void) | undefined
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      deliver = (chunk) => {
        controller.enqueue(chunk)
        controller.close()
      }
    }
  })
  const client = createHttpClient({
    fetch: () =>
      Promise.resolve(new Response(body, { headers: { 'Content-Type': 'application/json' } }))
  })
  const result = client.request('/auth/me', { decode: decodeString })
  await new Promise<void>((resolve) => setTimeout(resolve, 0))
  client.invalidate()
  assert.ok(deliver)
  deliver(new TextEncoder().encode('"old identity"'))
  await assert.rejects(result, { name: 'AbortError' })
})
