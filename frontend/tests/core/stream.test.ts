import assert from 'node:assert/strict'
import { test } from 'node:test'
import { acceptSnapshot, consumeSnapshots, parseRunSnapshot } from '../../src/shared/stream.ts'
const snapshot = (overrides: Record<string, unknown> = {}) => ({
  id: 'run-1',
  resource_type: 'chat',
  resource_id: 'conversation-1',
  status: 'running',
  attempt_id: 'a1',
  attempt_no: 1,
  revision: 2,
  projection_seq: 1,
  text: '你好',
  error: null,
  created_at: '2026-10-23T00:00:00Z',
  usage: { input_tokens: null, output_tokens: null, status: 'pending' },
  ...overrides
})
void test('SSE handles split UTF8, CRLF, comments and replaces full snapshots', async () => {
  const bytes = new TextEncoder().encode(
    ':keepalive\r\nevent: snapshot\r\ndata: ' +
      JSON.stringify(snapshot()) +
      '\r\n\r\nevent: snapshot\ndata: ' +
      JSON.stringify(snapshot({ status: 'completed', revision: 3, text: '你好，世界' })) +
      '\n\n'
  )
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let i = 0; i < bytes.length; i += 7) controller.enqueue(bytes.slice(i, i + 7))
      controller.close()
    }
  })
  const texts: string[] = []
  await consumeSnapshots(
    new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    (run) => {
      texts.push(run.text)
    },
    new AbortController().signal
  )
  assert.deepEqual(texts, ['你好', '你好，世界'])
})
void test('snapshot boundary rejects invalid fields and unknown event types', async () => {
  assert.throws(() => parseRunSnapshot(snapshot({ revision: '2' })), /快照/)
  assert.throws(() => parseRunSnapshot(snapshot({ status: 'magical' })), /快照/)
  await assert.rejects(
    consumeSnapshots(
      new Response('event: unknown\ndata: {}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' }
      }),
      () => {},
      new AbortController().signal
    ),
    /事件/
  )
})
void test('old revisions and attempts cannot overwrite newer snapshots', () => {
  const current = parseRunSnapshot(
    snapshot({ attempt_no: 2, attempt_id: 'a2', revision: 7, projection_seq: 4 })
  )
  assert.equal(
    acceptSnapshot(current, parseRunSnapshot(snapshot({ attempt_no: 1, revision: 100 }))),
    false
  )
  assert.equal(
    acceptSnapshot(
      current,
      parseRunSnapshot(snapshot({ attempt_no: 2, attempt_id: 'a2', revision: 6 }))
    ),
    false
  )
  assert.equal(
    acceptSnapshot(
      current,
      parseRunSnapshot(snapshot({ attempt_no: 2, attempt_id: 'stale', revision: 8 }))
    ),
    false
  )
  assert.equal(
    acceptSnapshot(
      current,
      parseRunSnapshot(snapshot({ attempt_no: 2, attempt_id: 'a2', revision: 8 }))
    ),
    true
  )
})
void test('aborting a stalled stream cancels reader without sending server cancellation', async () => {
  let cancelled = false
  const stream = new ReadableStream<Uint8Array>({
    cancel() {
      cancelled = true
    }
  })
  const controller = new AbortController()
  const reading = consumeSnapshots(
    new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }),
    () => {},
    controller.signal
  )
  controller.abort()
  await assert.rejects(reading, { name: 'AbortError' })
  assert.equal(cancelled, true)
})

void test('disconnected subscription restores server snapshot without starting another run', async () => {
  const { followRun } = await import('../../src/shared/stream.ts')
  let opens = 0
  let gets = 0
  const completed = parseRunSnapshot(snapshot({ status: 'completed', revision: 4, text: '已恢复' }))
  const received: string[] = []
  const result = await followRun(
    {
      open: () => {
        opens += 1
        return Promise.resolve(
          new Response('event: snapshot\ndata: ' + JSON.stringify(snapshot()) + '\n\n', {
            headers: { 'Content-Type': 'text/event-stream' }
          })
        )
      },
      get: () => {
        gets += 1
        return Promise.resolve(completed)
      }
    },
    parseRunSnapshot(snapshot()),
    (run) => {
      received.push(run.text)
    },
    new AbortController().signal
  )
  assert.equal(opens, 1)
  assert.equal(gets, 1)
  assert.equal(result.status, 'completed')
  assert.deepEqual(received, ['已恢复'])
})
