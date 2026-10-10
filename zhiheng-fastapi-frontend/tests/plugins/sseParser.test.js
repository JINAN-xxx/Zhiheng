import assert from 'node:assert/strict'

import { parsePlatformSSE } from '../../src/utils/platformSse.js'
import { parsePlatformSSE as legacyParser } from '../../plugins/ai/utils/sseParser.js'

assert.equal(legacyParser, parsePlatformSSE, 'The legacy entry must export the same shared function')

const encoder = new TextEncoder()
const source = [
  encoder.encode('event: content\ndata: {"delta":"你'),
  encoder.encode('好"}\r\n\r\n: keepalive\r\nevent: reasoning\ndata: {"delta":"思考"}\r\n\r\n'),
  encoder.encode('event: message\ndata: ["first",\n'),
  encoder.encode('data: "second"]\n\nevent: usage\ndata: {"inputTokens":2,\n'),
  encoder.encode('data: "outputTokens":3}\n\nevent: done\ndata: [DONE]\n\n'),
]

const events = []
for await (const event of parsePlatformSSE(source)) events.push(event)
assert.deepEqual(events, [
  { event: 'content', data: { delta: '你好' } },
  { event: 'reasoning', data: { delta: '思考' } },
  { event: 'message', data: ['first', 'second'] },
  { event: 'usage', data: { inputTokens: 2, outputTokens: 3 } },
  { event: 'done', data: { status: 'completed' } },
])

const tail = []
for await (const event of parsePlatformSSE([encoder.encode('event: meta\ndata: {"sessionId":"ses_1"}')])) {
  tail.push(event)
}
assert.deepEqual(tail, [{ event: 'meta', data: { sessionId: 'ses_1' } }])
const collect = async source => {
  const result = []
  for await (const event of parsePlatformSSE(source)) result.push(event)
  return result
}
const failures = []
async function check(name, run) {
  try {
    await run()
    console.log('ok SSE: ' + name)
  } catch (error) {
    failures.push({ name, error })
    console.error('not ok SSE: ' + name + ': ' + error.message)
  }
}

await check('all byte split positions preserve CRLF and multibyte UTF-8', async () => {
  const bytes = encoder.encode(': ping\r\nevent: content\r\ndata: {"delta":"中文🙂"}\r\n\r\nevent: usage\r\ndata: {"inputTokens":2}\r\n\r\n')
  const expected = [
    { event: 'content', data: { delta: '中文🙂' } },
    { event: 'usage', data: { inputTokens: 2 } },
  ]
  for (let offset = 1; offset < bytes.length; offset++) {
    assert.deepEqual(await collect([bytes.slice(0, offset), bytes.slice(offset)]), expected, `split at byte ${offset}`)
  }
  assert.deepEqual(await collect([...bytes].map(byte => new Uint8Array([byte]))), expected)
})

await check('CR, default events, primitive JSON and terminal marker retain the public shape', async () => {
  assert.deepEqual(await collect([encoder.encode('id: ignored\r: ping\rdata: 0\r\revent:\rdata: null\r\rdata: [DONE]\r\r')]), [
    { event: 'message', data: { value: 0 } },
    { event: 'message', data: { value: null } },
    { event: 'done', data: { status: 'completed' } },
  ])
})

await check('normal stream EOF releases the lock without cancelling a completed source', async () => {
  let cancelled = 0
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('event: content\ndata: {"delta":"done"}\n\n'))
      controller.close()
    },
    cancel() { cancelled++ },
  })
  assert.deepEqual(await collect(stream), [{ event: 'content', data: { delta: 'done' } }])
  assert.equal(stream.locked, false)
  assert.equal(cancelled, 0)
})

for (const malformed of [false, true]) {
  await check(`${malformed ? 'invalid JSON' : 'early return'} cancels the reader and releases its lock`, async () => {
    let cancelled = 0
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(malformed ? 'data: not-json\n\n' : 'event: meta\ndata: {}\n\n'))
      },
      cancel() { cancelled++ },
    })
    const iterator = parsePlatformSSE(stream)
    if (malformed) await assert.rejects(iterator.next(), /平台 SSE 数据无效/)
    else {
      assert.deepEqual((await iterator.next()).value, { event: 'meta', data: {} })
      await iterator.return()
    }
    assert.equal(cancelled, 1)
    assert.equal(stream.locked, false)
  })
}

await check('cleanup rejection cannot replace the original parse failure', async () => {
  let cancelled = 0
  const stream = new ReadableStream({
    start(controller) { controller.enqueue(encoder.encode('data: not-json\n\n')) },
    cancel() { cancelled++; throw new Error('cleanup failed') },
  })
  await assert.rejects(collect(stream), /平台 SSE 数据无效/)
  assert.equal(cancelled, 1)
  assert.equal(stream.locked, false)
})

await check('source read failures and aborts propagate with their identity intact', async () => {
  for (const error of [new Error('read failed'), new DOMException('Aborted', 'AbortError')]) {
    let producer
    const stream = new ReadableStream({ start(controller) { producer = controller } })
    const pending = parsePlatformSSE(stream).next()
    producer.error(error)
    await assert.rejects(pending, caught => caught === error)
    assert.equal(stream.locked, false)
  }
})

await check('early return closes an async iterable source', async () => {
  let closed = 0
  async function* chunks() {
    try {
      yield encoder.encode('event: meta\ndata: {}\n\n')
      assert.fail('The next upstream chunk must not be requested')
    } finally {
      closed++
    }
  }
  const iterator = parsePlatformSSE(chunks())
  await iterator.next()
  await iterator.return()
  assert.equal(closed, 1)
})

if (failures.length) {
  throw new AggregateError(failures.map(item => item.error), failures.map(item => item.name).join('; '))
}
