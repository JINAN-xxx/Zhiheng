import assert from 'node:assert/strict'
import { after, test } from 'node:test'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const { sendMessage } = await import('../../src/modules/chat/api/index.ts')
const { regenerateMessage } = await import('../../src/modules/chat/api/messageActions.ts')
const snapshot = {
  id: 'run-1',
  resource_type: 'chat',
  resource_id: 'c1',
  status: 'pending',
  attempt_id: null,
  attempt_no: 0,
  revision: 0,
  projection_seq: 0,
  text: '',
  error: null,
  created_at: '2026-11-13T00:00:00Z',
  usage: { input_tokens: null, output_tokens: null, status: 'pending' }
}

void test('ordinary sends and regeneration accept only Run snapshots and send no knowledge selection', async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = []
  fetcher = (input, init) => {
    assert.ok(typeof init?.body === 'string')
    calls.push({
      path: typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
      body: JSON.parse(init.body) as Record<string, unknown>
    })
    return Promise.resolve(new Response(JSON.stringify(snapshot)))
  }
  const controller = new AbortController()
  const sent = await sendMessage(
    'c1',
    { model_id: 'm1', message: 'hello', client_request_id: 'request-1' },
    controller.signal
  )
  const regenerated = await regenerateMessage('c1', 'a1', 'request-2', controller.signal)
  assert.equal(sent.id, snapshot.id)
  assert.equal(regenerated.id, snapshot.id)
  assert.equal(calls.length, 2)
  assert.equal('knowledge_base_ids' in calls[0]!.body, false)
  assert.equal(calls[1]!.path, '/api/chat/conversations/c1/messages/a1/regenerate')
})

void test('a knowledge-preparation response cannot enter the ordinary chat state machine', async () => {
  fetcher = () =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          kind: 'knowledge_preparation',
          id: 'prep-1',
          resource_type: 'chat',
          resource_id: 'c1',
          status: 'waiting_models',
          phase: 'embedding',
          run: null,
          hits: [],
          error: null,
          created_at: '2026-11-13T00:00:00Z',
          updated_at: '2026-11-13T00:00:00Z'
        })
      )
    )
  const controller = new AbortController()
  await assert.rejects(
    sendMessage(
      'c1',
      { model_id: 'm1', message: 'hello', client_request_id: 'request-1' },
      controller.signal
    ),
    /运行快照格式不正确/
  )
  await assert.rejects(
    regenerateMessage('c1', 'a1', 'request-2', controller.signal),
    /运行快照格式不正确/
  )
})
