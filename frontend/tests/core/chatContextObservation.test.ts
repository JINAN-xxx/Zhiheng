import { fullChatContext } from './chatContextFixture.ts'
import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, ref } from 'vue'
import type { Conversation } from '../../src/modules/chat/api/index.ts'
import type { RunSnapshot } from '../../src/shared/stream.ts'

const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
const { restoreIdentity, clearIdentity } = await import('../../src/modules/identity/public.ts')
const { http } = await import('../../src/shared/http.ts')
const { useChatContextObservation } = await import(
  '../../src/modules/chat/composables/contextObservation.ts'
)
after(() => {
  globalThis.fetch = originalFetch
  if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument)
  else Reflect.deleteProperty(globalThis, 'document')
})
const stamp = '2026-11-13T00:00:00Z'
const full = fullChatContext

const conversation = (id: string): Conversation => ({
  id,
  title: id,
  version: 1,
  archived: false,
  created_at: stamp
})
const run = (status: RunSnapshot['status']): RunSnapshot => ({
  id: 'run-1',
  resource_id: 'c1',
  resource_type: 'chat',
  status,
  attempt_id: 'attempt-1',
  attempt_no: 1,
  revision: 1,
  projection_seq: 1,
  text: '',
  error: null,
  created_at: stamp,
  usage: { input_tokens: null, output_tokens: null, status: 'pending' }
})
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
async function mount(t: TestContext) {
  const pageEvents = new EventTarget()
  const documentSource = Object.assign(new EventTarget(), {
    visibilityState: 'visible',
    defaultView: pageEvents
  })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: documentSource })
  const selected = ref<Conversation | null>(null)
  const active = ref<RunSnapshot | null>(null)
  const state: {
    value: typeof full
    reads: string[]
    respond?: (path: string) => Response | Promise<Response> | undefined
  } = { value: { ...full }, reads: [] }
  fetcher = (input) => {
    const url = new URL(input instanceof Request ? input.url : input, 'http://fixture')
    if (url.pathname === '/api/auth/me')
      return Promise.resolve(
        new Response(JSON.stringify({ id: 'owner-1', username: 'owner', is_admin: true }))
      )
    assert.ok(url.pathname.endsWith('/context'))
    state.reads.push(url.pathname)
    return Promise.resolve(
      state.respond?.(url.pathname) ?? new Response(JSON.stringify(state.value))
    )
  }
  await restoreIdentity(true)
  t.after(clearIdentity)
  let observation!: ReturnType<typeof useChatContextObservation>
  const renderer = createRenderer({
    patchProp() {},
    insert() {},
    remove() {},
    createElement: () => ({}),
    createText: () => ({}),
    createComment: () => ({}),
    setText() {},
    setElementText() {},
    parentNode: () => null,
    nextSibling: () => null
  })
  const app = renderer.createApp({
    setup() {
      observation = useChatContextObservation(selected, active)
      return () => null
    }
  })
  app.mount({})
  t.after(() => app.unmount())
  selected.value = conversation('c1')
  await flush()
  return { selected, active, state, documentSource, pageEvents, observation }
}

void test('a full context is observed throughout an active Run and stops at its final summary', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = await mount(t)
  assert.equal(fixture.observation.contextNotice.value, '')
  fixture.active.value = run('pending')
  await flush()
  const pendingReads = fixture.state.reads.length
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(fixture.state.reads.length, pendingReads + 1)
  fixture.active.value = run('running')
  await flush()
  const runningReads = fixture.state.reads.length
  fixture.active.value = { ...run('running'), revision: 2, text: 'partial' }
  await flush()
  assert.equal(fixture.state.reads.length, runningReads)
  fixture.state.value = { ...full, status: 'summarizing' }
  t.mock.timers.tick(1500)
  await flush()
  assert.match(fixture.observation.contextNotice.value, /正在按 Token 预算整理/)
  fixture.state.value = { ...full, status: 'summarized' }
  fixture.active.value = run('completed')
  await flush()
  assert.equal(fixture.observation.contextNotice.value, '已按 Token 预算整理较早对话')
  const finalReads = fixture.state.reads.length
  t.mock.timers.tick(4500)
  await flush()
  assert.equal(fixture.state.reads.length, finalReads)
})

void test('active context observation pauses when hidden and stays scoped to the selected conversation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = await mount(t)
  fixture.active.value = run('running')
  await flush()
  fixture.documentSource.visibilityState = 'hidden'
  fixture.documentSource.dispatchEvent(new Event('visibilitychange'))
  const hiddenReads = fixture.state.reads.length
  t.mock.timers.tick(4500)
  await flush()
  assert.equal(fixture.state.reads.length, hiddenReads)
  fixture.documentSource.visibilityState = 'visible'
  fixture.documentSource.dispatchEvent(new Event('visibilitychange'))
  await flush()
  assert.equal(fixture.state.reads.length, hiddenReads + 1)
  fixture.selected.value = conversation('c2')
  await flush()
  assert.equal(fixture.state.reads.at(-1), '/api/chat/conversations/c2/context')
  const switchedReads = fixture.state.reads.length
  t.mock.timers.tick(4500)
  await flush()
  assert.equal(fixture.state.reads.length, switchedReads)
  fixture.selected.value = null
  await flush()
  t.mock.timers.tick(4500)
  await flush()
  assert.equal(fixture.state.reads.length, switchedReads)
})

for (const status of [403, 404])
  void test(`HTTP ${status} pauses current-run retries until focus or a new scope`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const fixture = await mount(t)
    fixture.state.respond = () => new Response('{}', { status })
    fixture.active.value = run('pending')
    await flush()
    assert.match(
      fixture.observation.contextNotice.value,
      status === 403 ? /无权/ : /不存在或当前不可访问/
    )
    assert.doesNotMatch(fixture.observation.contextNotice.value, /可继续聊天/)
    const failedReads = fixture.state.reads.length
    t.mock.timers.tick(60000)
    await flush()
    fixture.active.value = run('running')
    await flush()
    assert.equal(fixture.state.reads.length, failedReads)
    delete fixture.state.respond
    fixture.pageEvents.dispatchEvent(new Event('focus'))
    await flush()
    assert.equal(fixture.observation.contextNotice.value, '')
    assert.equal(fixture.state.reads.length, failedReads + 1)
    fixture.state.respond = () => new Response('{}', { status })
    fixture.active.value = { ...run('pending'), id: 'run-2' }
    await flush()
    assert.equal(fixture.state.reads.length, failedReads + 2)
    delete fixture.state.respond
    fixture.selected.value = conversation('c2')
    await flush()
    assert.equal(fixture.observation.contextNotice.value, '')
    assert.equal(fixture.state.reads.at(-1), '/api/chat/conversations/c2/context')
  })

void test('network, 429 and server errors back off and recover within the same conversation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = await mount(t)
  let attempts = 0
  fixture.state.respond = () => {
    attempts += 1
    if (attempts === 1) return Promise.reject(new TypeError('Failed to fetch'))
    if (attempts === 2) return new Response('{}', { status: 429 })
    if (attempts === 3) return new Response('{}', { status: 503 })
    return new Response(JSON.stringify(full))
  }
  fixture.active.value = run('running')
  await flush()
  assert.equal(attempts, 1)
  for (const delay of [1500, 3000, 6000]) {
    const before: number = attempts
    t.mock.timers.tick(delay - 1)
    await flush()
    assert.equal(attempts, before)
    t.mock.timers.tick(1)
    await flush()
    assert.equal(attempts, before + 1)
  }
  assert.equal(fixture.observation.contextNotice.value, '')
})

void test('invalid context data stops automatic polling without hiding a new-run recovery', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = await mount(t)
  fixture.state.respond = () => new Response(JSON.stringify({ ...full, strategy: 'rounds' }))
  fixture.active.value = run('running')
  await flush()
  assert.match(fixture.observation.contextNotice.value, /数据异常/)
  const failedReads = fixture.state.reads.length
  t.mock.timers.tick(60000)
  await flush()
  assert.equal(fixture.state.reads.length, failedReads)
  delete fixture.state.respond
  fixture.active.value = { ...run('pending'), id: 'run-2' }
  await flush()
  assert.equal(fixture.observation.contextNotice.value, '')
  assert.equal(fixture.state.reads.length, failedReads + 1)
})

void test('a late permanent error cannot pause the newly selected conversation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = await mount(t)
  let release!: (value: Response) => void
  fixture.state.respond = (path) =>
    path.includes('/c1/')
      ? new Promise((resolve) => {
          release = resolve
        })
      : undefined
  fixture.active.value = run('running')
  await flush()
  fixture.selected.value = conversation('c2')
  await flush()
  release(new Response('{}', { status: 404 }))
  await flush()
  assert.equal(fixture.observation.contextNotice.value, '')
  fixture.active.value = { ...run('running'), id: 'run-2', resource_id: 'c2' }
  await flush()
  const activeReads = fixture.state.reads.length
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(fixture.state.reads.length, activeReads + 1)
})

void test('HTTP 401 delegates to the existing session invalidation handler', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = await mount(t)
  let invalidations = 0
  http.setUnauthorizedHandler(() => {
    invalidations += 1
    clearIdentity()
  })
  t.after(() => http.setUnauthorizedHandler(() => {}))
  fixture.state.respond = () => new Response('{}', { status: 401 })
  fixture.active.value = run('running')
  await flush()
  assert.equal(invalidations, 1)
  assert.equal(fixture.observation.contextNotice.value, '')
  const failedReads = fixture.state.reads.length
  t.mock.timers.tick(60000)
  await flush()
  assert.equal(fixture.state.reads.length, failedReads)
})
