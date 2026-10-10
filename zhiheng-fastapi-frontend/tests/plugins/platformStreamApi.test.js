import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { loadPlatformStreamApis } from './helpers/loadPlatformStreamApis.js'

const sharedPath = new URL('../../src/utils/platformSse.js', import.meta.url)
assert.ok(existsSync(sharedPath), 'The platform must own the shared SSE parser')
const encoder = new TextEncoder()
const frames = 'event: meta\r\ndata: {"runId":"run-a"}\r\n\r\nevent: content\r\ndata: {"delta":"你好🙂"}\r\n\r\nevent: error\r\ndata: {"code":"FAILED"}\r\n\r\nevent: done\r\ndata: {"status":"failed"}\r\n\r\n'
const expected = [
  { event: 'meta', data: { runId: 'run-a' } },
  { event: 'content', data: { delta: '你好🙂' } },
  { event: 'error', data: { code: 'FAILED' } },
  { event: 'done', data: { status: 'failed' } },
]
const collect = async iterator => {
  const events = []
  for await (const event of iterator) events.push(event)
  return events
}
const response = body => ({ ok: true, body, headers: new Headers({ 'Content-Type': 'text/event-stream' }) })

for (const kind of ['chat', 'work']) {
  let fetchImpl
  const modules = await loadPlatformStreamApis((...args) => fetchImpl(...args))
  const payload = { messages: [{ role: 'user', content: 'hello' }], agentId: 'agent-a', message: 'hello', clientMessageId: 'request-a' }
  const invoke = signal => kind === 'chat'
    ? modules.chat.createChatStream(payload, signal)
    : modules.work.createWorkTaskRunStream('task-a', payload, signal)
  const controller = new AbortController()
  fetchImpl = async (url, config) => {
    assert.equal(url, kind === 'chat' ? '/test-api/ai/chat/send' : '/test-api/agent_extension/work-tasks/task-a/runs')
    assert.equal(config.method, 'POST')
    assert.equal(config.headers.Authorization, 'Bearer test-token')
    assert.equal(config.headers['Content-Type'], 'application/json')
    assert.equal(config.signal, controller.signal)
    assert.deepEqual(JSON.parse(config.body), kind === 'chat' ? payload : {
      agentId: 'agent-a', message: 'hello', clientMessageId: 'request-a',
    })
    return response(new ReadableStream({
      start(producer) {
        for (const byte of encoder.encode(frames)) producer.enqueue(new Uint8Array([byte]))
        producer.close()
      },
    }))
  }
  assert.deepEqual(await collect(await invoke(controller.signal)), expected, `${kind} preserves typed events`)

  let cancelled = 0
  const earlyBody = new ReadableStream({
    start(producer) { producer.enqueue(encoder.encode('event: meta\ndata: {}\n\n')) },
    cancel() { cancelled++ },
  })
  fetchImpl = async () => response(earlyBody)
  const early = await invoke(controller.signal)
  assert.deepEqual((await early.next()).value, { event: 'meta', data: {} })
  await early.return()
  assert.equal(cancelled, 1)
  assert.equal(earlyBody.locked, false)

  fetchImpl = async () => ({ ...response(null), ok: false, status: 403, json: async () => ({ detail: { code: 'DENIED', message: 'blocked' } }) })
  await assert.rejects(invoke(controller.signal), error => error.code === 'DENIED' && error.status === 403 && error.message === 'blocked')
  fetchImpl = async () => ({ ...response(null), ok: false, status: 502, json: async () => { throw new SyntaxError('proxy HTML') } })
  await assert.rejects(invoke(controller.signal), error => error.status === 502 && error.code === (kind === 'chat' ? 'CHAT_REQUEST_FAILED' : 'WORK_RUN_FAILED'))
  fetchImpl = async () => response(null)
  await assert.rejects(invoke(controller.signal), kind === 'chat' ? /对话响应为空/ : /工作执行响应为空/)

  if (kind === 'work') {
    fetchImpl = async () => ({
      ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }),
      json: async () => ({ msg: 'stream unavailable' }),
    })
    await assert.rejects(invoke(controller.signal), error => error.code === 'WORK_RUN_FAILED' && error.message === 'stream unavailable')
  }

  const abort = new DOMException('Aborted', 'AbortError')
  fetchImpl = async () => { throw abort }
  await assert.rejects(invoke(controller.signal), error => error === abort)

  const abortController = new AbortController()
  let pendingBody
  fetchImpl = async (_, { signal }) => {
    pendingBody = new ReadableStream({
      start(producer) { signal.addEventListener('abort', () => producer.error(abort), { once: true }) },
    })
    return response(pendingBody)
  }
  const active = await invoke(abortController.signal)
  const reading = active.next()
  abortController.abort()
  await assert.rejects(reading, error => error === abort)
  assert.equal(pendingBody.locked, false)
}

const sources = [
  '../../plugins/ai/api/chat.js',
  '../../plugins/agent_extension/api/workTask.js',
]
for (const source of sources) {
  const code = readFileSync(new URL(source, import.meta.url), 'utf8')
  assert.match(code, /import\s*\{\s*parsePlatformSSE\s*\}\s*from\s*['"]@\/utils\/platformSse(?:\.js)?['"]/, source)
  assert.doesNotMatch(code, /from\s*['"][^'"]*sseParser/, source)
}
assert.doesNotMatch(readFileSync(sharedPath, 'utf8'), /from\s*['"][^'"]*plugins\//)
console.log('ok Chat/Work shared SSE: requests, byte boundaries, early close, error and AbortSignal behavior')
