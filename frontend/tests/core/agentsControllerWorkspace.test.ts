import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

const originalFetch = globalThis.fetch
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
const { restoreIdentity, clearIdentity } = await import('../../src/modules/identity/public.ts')
const { useAssistantWorkspace } = await import(
  '../../src/modules/agents/composables/useAssistantWorkspace.ts'
)
after(() => {
  globalThis.fetch = originalFetch
  if (originalStorage) Object.defineProperty(globalThis, 'sessionStorage', originalStorage)
  else Reflect.deleteProperty(globalThis, 'sessionStorage')
})
const stamp = '2026-11-15T00:00:00Z'
const response = (value: unknown) => new Response(JSON.stringify(value))
const deferred = <T>() => {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((accept) => {
    resolve = accept
  })
  return { promise, resolve }
}
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
class MemoryStorage implements Storage {
  private values = new Map<string, string>()
  get length() {
    return this.values.size
  }
  getItem(key: string) {
    return this.values.get(key) ?? null
  }
  setItem(key: string, value: string) {
    this.values.set(key, value)
  }
  removeItem(key: string) {
    this.values.delete(key)
  }
  clear() {
    this.values.clear()
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null
  }
}
const config = {
  name: '助手',
  description: '',
  model_id: 'model-1',
  system_prompt: '',
  welcome_message: '',
  skill_slugs: [],
  knowledge_base_ids: [],
  application_id: null,
  temperature: 0.7,
  max_tokens: 512,
  history_rounds: 20
}
const conversation = (id: string) => ({
  id,
  agent_id: 'agent-1',
  title: id,
  version: 1,
  updated_at: stamp,
  name: '助手',
  description: '',
  welcome_message: '',
  created_at: stamp,
  preparation_id: null,
  memory_available: true,
  summary_used: false
})
const run = (id: string) => ({
  id: 'run-' + id,
  resource_type: 'agent_chat',
  resource_id: id,
  status: 'completed',
  attempt_id: 'attempt-1',
  attempt_no: 1,
  revision: 2,
  projection_seq: 2,
  text: '已回答',
  error: null,
  created_at: stamp,
  usage: { input_tokens: 1, output_tokens: 1, status: 'known' }
})

class Fixture {
  owner = 'owner-1'
  requests: { url: URL; init?: RequestInit | undefined }[] = []
  admissions: { message: string; client_request_id: string }[] = []
  creations: { client_request_id: string }[] = []
  storage = new MemoryStorage()
  override?: (url: URL, init?: RequestInit) => Response | Promise<Response> | undefined
  constructor(t: TestContext) {
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: this.storage })
    t.after(clearIdentity)
    fetcher = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, 'http://fixture')
      this.requests.push({ url, init })
      const custom = this.override?.(url, init)
      if (custom) return custom
      if (url.pathname === '/api/auth/me')
        return response({
          id: this.owner,
          username: this.owner,
          is_admin: false,
          permissions: ['platform:agents:enabled', 'platform:agents:read', 'platform:agents:use']
        })
      if (url.pathname === '/api/agents/agent-1')
        return response({
          ...config,
          id: 'agent-1',
          owner_id: this.owner,
          version: 2,
          status: 'enabled'
        })
      if (url.pathname === '/api/agents/agent-1/conversations') {
        if (init?.method === 'POST') {
          assert.ok(typeof init.body === 'string')
          this.creations.push(JSON.parse(init.body) as { client_request_id: string })
          return response(conversation('c1'))
        }
        return response([conversation('c1'), conversation('c2')])
      }
      if (url.pathname.endsWith('/runs')) {
        assert.ok(typeof init?.body === 'string')
        this.admissions.push(
          JSON.parse(init.body) as { message: string; client_request_id: string }
        )
        return response(run('c1'))
      }
      const id = url.pathname.split('/').at(-1)
      if (id === 'c1' || id === 'c2') return this.detail(id)
      throw new Error('Unexpected request: ' + url.pathname)
    }
  }
  detail(id: string) {
    return response({
      conversation: conversation(id),
      turns: [
        {
          id: 'turn-' + id,
          request_id: this.admissions.at(-1)?.client_request_id ?? 'old-' + id,
          sequence: 1,
          agent_name: '助手',
          content: id,
          run: run(id),
          sources: []
        }
      ],
      next_before: null,
      preparing_message: null
    })
  }
  async mount(t: TestContext, path = '/agents/agent-1/chat') {
    await restoreIdentity(true)
    let state!: ReturnType<typeof useAssistantWorkspace>
    const component = {
      setup() {
        state = useAssistantWorkspace()
        return () => null
      }
    }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/agents/:id/chat', name: 'assistant-chat', component }]
    })
    await router.push(path)
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
    const app = renderer.createApp(component)
    app.use(router)
    app.mount({})
    t.after(() => app.unmount())
    await flush()
    return { state, router, unmount: () => app.unmount() }
  }
}

void test('a lost admission reuses its persisted conversation and request identity after remount', async (t) => {
  const fixture = new Fixture(t)
  const first = await fixture.mount(t)
  fixture.override = (url, init) => {
    if (!url.pathname.endsWith('/runs')) return
    assert.ok(typeof init?.body === 'string')
    fixture.admissions.push(JSON.parse(init.body) as { message: string; client_request_id: string })
    return Promise.reject(new TypeError('lost response'))
  }
  first.state.draft.value = '原问题'
  await first.state.send()
  assert.equal(first.state.outgoing.value?.stage, 'uncertain')
  const original = first.state.pending.value
  assert.ok(original)
  first.unmount()
  delete fixture.override
  const second = await fixture.mount(t, '/agents/agent-1/chat?conversation=c1')
  assert.equal(second.state.pending.value?.requestId, original.requestId)
  await second.state.send(true)
  assert.equal(fixture.creations.length, 1)
  assert.equal(fixture.admissions.length, 2)
  assert.deepEqual(fixture.admissions[0], fixture.admissions[1])
  assert.equal(second.state.pending.value, null)
  assert.equal(fixture.storage.length, 0)
})

void test('a conversation switch rejects an old detail even when its fetch resolves late', async (t) => {
  const fixture = new Fixture(t)
  const mounted = await fixture.mount(t)
  const oldDetail = deferred<Response>()
  let oldSignal: AbortSignal | null | undefined
  fixture.override = (url, init) => {
    if (!url.pathname.endsWith('/c1')) return
    oldSignal = init?.signal
    return oldDetail.promise
  }
  mounted.state.select('c1')
  await flush()
  mounted.state.select('c2')
  await flush()
  assert.equal(mounted.state.selected.value?.id, 'c2')
  oldDetail.resolve(fixture.detail('c1'))
  await flush()
  assert.equal(oldSignal?.aborted, true)
  assert.equal(mounted.state.selected.value?.id, 'c2')
  assert.equal(mounted.state.turns.value[0]?.content, 'c2')
})

void test('identity replacement revokes an admission without clearing the original owner recovery record', async (t) => {
  const fixture = new Fixture(t)
  const mounted = await fixture.mount(t)
  const admission = deferred<Response>()
  let oldSignal: AbortSignal | null | undefined
  fixture.override = (url, init) => {
    if (!url.pathname.endsWith('/runs')) return
    oldSignal = init?.signal
    return admission.promise
  }
  mounted.state.draft.value = '原身份问题'
  const sending = mounted.state.send()
  await flush()
  assert.equal(mounted.state.sending.value, true)
  clearIdentity()
  fixture.owner = 'owner-2'
  await restoreIdentity(true)
  await flush()
  admission.resolve(response(run('c1')))
  await sending
  assert.equal(oldSignal?.aborted, true)
  assert.equal(mounted.state.pending.value, null)
  assert.equal(mounted.state.outgoing.value, null)
  assert.equal(mounted.state.sending.value, false)
  assert.ok(fixture.storage.getItem('zhiheng:assistant:owner-1:agent-1:pending'))
  assert.equal(fixture.storage.getItem('zhiheng:assistant:owner-2:agent-1:pending'), null)
})
