import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer } from 'vue'
import { message } from 'ant-design-vue'
import type { ChatModel } from '../../src/modules/models/api/index.ts'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const { restoreIdentity, clearIdentity } = await import('../../src/modules/identity/public.ts')
const { useModelAccess } = await import('../../src/modules/models/composables/useModelAccess.ts')
const { useModelTrial } = await import('../../src/modules/models/composables/useModelTrial.ts')
const model = (id: string): ChatModel => ({
  id,
  name: id,
  connection_id: 'connection-1',
  upstream_model: id,
  max_output_tokens: 32,
  enabled: true,
  model_type: 'chat',
  vision_enabled: false,
  capabilities: ['chat']
})
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
class Fixture {
  owner = 'owner-A'
  permissions = ['platform:models:enabled', 'platform:models:read', 'platform:models:test']
  calls: { owner: string; modelId: string; requestId: string }[] = []
  replies: (Response | Error)[] = []
  unmount = () => {}
  constructor() {
    fetcher = async (input, init) => {
      await Promise.resolve()
      const url = new URL(input instanceof Request ? input.url : input, 'http://fixture')
      if (url.pathname === '/api/auth/me')
        return json({
          id: this.owner,
          username: this.owner,
          is_admin: false,
          permissions: this.permissions
        })
      if (url.pathname.endsWith('/trial')) {
        assert.ok(typeof init?.body === 'string')
        const body = JSON.parse(init.body) as { client_request_id: string }
        const id = url.pathname.split('/')[3]!
        this.calls.push({ owner: this.owner, modelId: id, requestId: body.client_request_id })
        const reply = this.replies.shift()
        if (reply instanceof Error) throw reply
        if (reply) return reply
        return json({
          id: 'run-' + id,
          resource_type: 'connection_test',
          resource_id: id,
          status: 'completed',
          attempt_id: null,
          attempt_no: 0,
          revision: 1,
          projection_seq: 1,
          text: 'ok',
          error: null,
          created_at: '2026-11-16T00:00:00Z',
          usage: { input_tokens: 1, output_tokens: 1, status: 'known' }
        })
      }
      throw new Error('Unexpected fixture request: ' + url.pathname)
    }
  }
  async mount(t: TestContext) {
    t.mock.method(message, 'success', () => undefined)
    t.mock.method(message, 'error', () => undefined)
    t.mock.method(message, 'info', () => undefined)
    const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
    const saved = new Map<string, string>()
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => saved.get(key) ?? null,
        setItem: (key: string, content: string) => saved.set(key, content),
        removeItem: (key: string) => saved.delete(key)
      }
    })
    t.after(() => {
      if (originalStorage) Object.defineProperty(globalThis, 'sessionStorage', originalStorage)
      else Reflect.deleteProperty(globalThis, 'sessionStorage')
    })
    await restoreIdentity(true)
    let state!: ReturnType<typeof useModelTrial>
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
        const access = useModelAccess()
        state = useModelTrial(
          {
            models: [model('A'), model('B')],
            get canTest() {
              return access.canTestModels.value
            },
            pending: {},
            connectionIssues: {}
          },
          { refresh: () => undefined, denied: access.denyTrialAccess }
        )
        return () => null
      }
    })
    app.mount({})
    let mounted = true
    this.unmount = () => {
      if (mounted) {
        mounted = false
        app.unmount()
      }
    }
    t.after(() => {
      this.unmount()
      clearIdentity()
    })
    return {
      async testModel(value: ChatModel, correctedPrompt?: string) {
        if (!state.open(value)) return
        if (correctedPrompt) state.prompt.value = correctedPrompt
        await state.submit()
      }
    }
  }
}

void test('unknown connection test A blocks B until exact A UUID is confirmed', async (t) => {
  const fixture = new Fixture()
  fixture.replies.push(new TypeError('response lost after acceptance'))
  const state = await fixture.mount(t)
  await state.testModel(model('A'))
  await state.testModel(model('B'))
  assert.deepEqual(
    fixture.calls.map((call) => call.modelId),
    ['A']
  )
  await state.testModel(model('A'))
  assert.equal(fixture.calls.length, 2)
  assert.equal(fixture.calls[0]?.requestId, fixture.calls[1]?.requestId)
  await state.testModel(model('B'))
  assert.deepEqual(
    fixture.calls.map((call) => call.modelId),
    ['A', 'A', 'B']
  )
})

void test('a same-owner grant refresh preserves the unknown connection test identity', async (t) => {
  const fixture = new Fixture()
  fixture.replies.push(new TypeError('response lost'))
  const state = await fixture.mount(t)
  await state.testModel(model('A'))
  fixture.permissions.push('platform:models:config_read')
  await restoreIdentity(true)
  await flush()
  await state.testModel(model('B'))
  assert.equal(fixture.calls.length, 1)
  await state.testModel(model('A'))
  assert.equal(fixture.calls[0]?.requestId, fixture.calls[1]?.requestId)
})

void test('owner replacement and unmount revoke old connection test recovery closures', async (t) => {
  const fixture = new Fixture()
  fixture.replies.push(new TypeError('response lost'))
  const state = await fixture.mount(t)
  await state.testModel(model('A'))
  fixture.owner = 'owner-B'
  await restoreIdentity(true)
  await flush()
  await state.testModel(model('A'))
  assert.equal(fixture.calls[1]?.owner, 'owner-B')
  assert.notEqual(fixture.calls[0]?.requestId, fixture.calls[1]?.requestId)
  fixture.unmount()
  await state.testModel(model('B'))
  assert.equal(fixture.calls.length, 2)
})

void test('a definitive connection test rejection releases the intent for a corrected fresh attempt', async (t) => {
  const fixture = new Fixture()
  fixture.replies.push(
    json({ error: { code: 'model_not_found', message: 'model unavailable' } }, 404)
  )
  const state = await fixture.mount(t)
  await state.testModel(model('A'))
  await state.testModel(model('A'), 'Corrected input.')
  assert.equal(fixture.calls.length, 2)
  assert.notEqual(fixture.calls[0]?.requestId, fixture.calls[1]?.requestId)
})

void test('temporarily revoking and restoring test permission cannot discard the same owner unknown UUID', async (t) => {
  const fixture = new Fixture()
  fixture.replies.push(new TypeError('accepted response lost'))
  const state = await fixture.mount(t)
  await state.testModel(model('A'))
  fixture.permissions = fixture.permissions.filter((code) => code !== 'platform:models:test')
  await restoreIdentity(true)
  await flush()
  await state.testModel(model('A'))
  assert.equal(fixture.calls.length, 1)
  fixture.permissions.push('platform:models:test')
  await restoreIdentity(true)
  await flush()
  await state.testModel(model('B'))
  assert.equal(fixture.calls.length, 1)
  await state.testModel(model('A'))
  assert.equal(fixture.calls.length, 2)
  assert.equal(fixture.calls[0]?.requestId, fixture.calls[1]?.requestId)
})

for (const status of [404, 422]) {
  void test(`HTTP ${status} cannot release a connection test UUID that was already unknown`, async (t) => {
    const fixture = new Fixture()
    fixture.replies.push(new TypeError('accepted response lost'))
    const state = await fixture.mount(t)
    await state.testModel(model('A'))
    fixture.replies.push(
      json({ error: { code: 'temporarily_unavailable', message: 'confirmation rejected' } }, status)
    )
    await state.testModel(model('A'))
    assert.equal(fixture.calls.length, 2)
    await state.testModel(model('B'))
    assert.equal(fixture.calls.length, 2)
    await state.testModel(model('A'))
    assert.equal(fixture.calls.length, 3)
    assert.deepEqual(fixture.calls[1], fixture.calls[0])
    assert.deepEqual(fixture.calls[2], fixture.calls[0])
  })
}
