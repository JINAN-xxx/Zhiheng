import { grantedPermissions } from './permissionFixture.ts'
import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
  if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument)
  else Reflect.deleteProperty(globalThis, 'document')
})
const { provideChatWorkspace, useChatWorkspace } = await import(
  '../../src/modules/chat/composables/workspace.ts'
)
Object.defineProperty(globalThis, 'document', {
  configurable: true,
  value: Object.assign(new EventTarget(), { visibilityState: 'visible' })
})
const stamp = '2026-11-10T00:00:00Z'
const conversation = (id: string) => ({
  id,
  title: id,
  created_at: stamp,
  preparation_id: null,
  version: 1,
  archived: false
})
const message = (id: string, second: number) => ({
  id,
  role: 'assistant',
  content: id,
  run_id: 'run-' + id,
  created_at: `2026-11-10T00:00:${String(second).padStart(2, '0')}Z`,
  is_complete: true
})
const response = (value: unknown, cursor = '') =>
  new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json', 'X-Next-Cursor': cursor }
  })
const flush = async () => {
  await setImmediate()
  await setImmediate()
}

class Fixture {
  requests: URL[] = []
  override?: (url: URL, init?: RequestInit) => Promise<Response | undefined> | Response | undefined
  constructor() {
    fetcher = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, 'http://fixture')
      this.requests.push(url)
      const custom = await this.override?.(url, init)
      if (custom) return custom
      if (url.pathname === '/api/auth/me')
        return response({
          id: 'user-1',
          username: 'test',
          is_admin: false,
          permissions: grantedPermissions('platform:chat:access')
        })
      if (url.pathname === '/api/models') return response([])
      if (url.pathname === '/api/chat/conversations')
        return response([conversation('recent')], 'next-conversations')
      if (url.pathname.endsWith('/messages'))
        return response([message('recent-message', 30)], 'older-messages')
      if (url.pathname.endsWith('/context')) return response({ status: 'full' })
      throw new Error('Unexpected request: ' + url.toString())
    }
  }
  async mount(t: TestContext) {
    const { restoreIdentity, clearIdentity } = await import('../../src/modules/identity/public.ts')
    await restoreIdentity(true)
    t.after(clearIdentity)
    let state!: ReturnType<typeof useChatWorkspace>
    const child = {
      setup() {
        state = useChatWorkspace()
        return () => null
      }
    }
    const component = {
      setup() {
        provideChatWorkspace()
        return () => h(child)
      }
    }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/chat', name: 'chat', component }]
    })
    await router.push('/chat')
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
    return state
  }
}

void test('conversation paging appends unique rows and search reaches the server', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture()
  fixture.override = (url) =>
    url.searchParams.has('cursor') && url.pathname === '/api/chat/conversations'
      ? response([conversation('recent'), conversation('old')])
      : url.searchParams.get('search') === 'old unseen'
        ? response([conversation('unseen result')])
        : undefined
  const state = await fixture.mount(t)
  await state.loadMoreConversations()
  assert.deepEqual(
    state.conversations.value.map((row) => row.id),
    ['recent', 'old']
  )
  assert.equal(state.conversationCursor.value, null)
  state.setHistorySearch('old unseen')
  t.mock.timers.tick(250)
  await flush()
  assert.equal(fixture.requests.at(-1)?.searchParams.get('search'), 'old unseen')
  assert.deepEqual(
    state.conversations.value.map((row) => row.id),
    ['unseen result']
  )
})

void test('changing the filter cancels an older page and rejects its late response', async (t) => {
  const fixture = new Fixture()
  const state = await fixture.mount(t)
  let release!: (value: Response) => void, signal: AbortSignal | null | undefined
  fixture.override = (url, init) => {
    if (url.searchParams.has('cursor'))
      return new Promise((resolve) => {
        release = resolve
        signal = init?.signal
      })
    if (url.searchParams.get('archived') === 'true') return response([conversation('archived')])
    return undefined
  }
  const pending = state.loadMoreConversations()
  await flush()
  state.setArchivedFilter(true)
  await flush()
  assert.equal(signal?.aborted, true)
  release(response([conversation('stale')]))
  await pending
  await flush()
  assert.deepEqual(
    state.conversations.value.map((row) => row.id),
    ['archived']
  )
})

void test('earlier messages prepend without duplicates and switching conversations rejects stale pages', async (t) => {
  const fixture = new Fixture(),
    state = await fixture.mount(t)
  await state.selectConversation(conversation('recent'))
  fixture.override = (url) =>
    url.searchParams.get('cursor') === 'older-messages'
      ? response([message('old-message', 1), message('recent-message', 30)], 'oldest')
      : undefined
  await state.loadOlderMessages()
  assert.deepEqual(
    state.messages.value.map((row) => row.id),
    ['old-message', 'recent-message']
  )
  assert.equal(state.olderCursor.value, 'oldest')
  let release!: (value: Response) => void
  fixture.override = (url) =>
    url.searchParams.get('cursor') === 'oldest'
      ? new Promise((resolve) => {
          release = resolve
        })
      : undefined
  const pending = state.loadOlderMessages()
  await flush()
  await state.selectConversation(conversation('other'))
  release(response([message('stale-old-message', 0)]))
  await pending
  assert.equal(state.selected.value?.id, 'other')
  assert.deepEqual(
    state.messages.value.map((row) => row.id),
    ['recent-message']
  )
})

void test('signing out while a page body is decoding rejects the late history data', async (t) => {
  const fixture = new Fixture(),
    state = await fixture.mount(t)
  let release!: (value: unknown) => void
  fixture.override = (url) => {
    if (!url.searchParams.has('cursor')) return undefined
    const value = response([])
    value.json = () =>
      new Promise((resolve) => {
        release = resolve
      })
    return value
  }
  const pending = state.loadMoreConversations()
  await flush()
  const { http } = await import('../../src/shared/http.ts')
  http.invalidate()
  release([conversation('signed-out-page')])
  await pending
  assert.deepEqual(
    state.conversations.value.map((row) => row.id),
    ['recent']
  )
})

void test('a generation refresh cannot discard an older page loaded while its response was pending', async (t) => {
  const fixture = new Fixture()
  let submitted = false,
    pendingTail = false,
    release!: (value: Response) => void
  const latest = [message('recent-message', 30), message('new-message', 31)]
  fixture.override = (url, init) => {
    if (url.pathname === '/api/auth/me')
      return response({
        id: 'user-1',
        username: 'test',
        is_admin: false,
        permissions: grantedPermissions('platform:chat:access')
      })
    if (url.pathname === '/api/models')
      return response([
        {
          id: 'model-1',
          name: 'Model',
          connection_id: 'connection-1',
          upstream_model: 'local',
          max_output_tokens: 100,
          model_type: 'chat',
          enabled: true,
          published: true,
          capabilities: ['chat', 'stream']
        }
      ])
    if (url.pathname.endsWith('/runs') && init?.method === 'POST') {
      submitted = pendingTail = true
      return response({
        id: 'new-run',
        resource_type: 'chat',
        resource_id: 'recent',
        status: 'completed',
        attempt_id: null,
        attempt_no: 0,
        revision: 1,
        projection_seq: 1,
        text: 'Done',
        error: null,
        created_at: stamp,
        usage: { input_tokens: 1, output_tokens: 1, status: 'known' }
      })
    }
    if (url.pathname.endsWith('/messages') && url.searchParams.has('cursor'))
      return response([message('old-message', 1)])
    if (url.pathname.endsWith('/messages') && submitted) {
      if (pendingTail) {
        pendingTail = false
        return new Promise((resolve) => {
          release = resolve
        })
      }
      return response(latest, 'older-messages')
    }
    return undefined
  }
  const { restoreIdentity, clearIdentity } = await import('../../src/modules/identity/public.ts')
  await restoreIdentity(true)
  t.after(clearIdentity)
  const state = await fixture.mount(t)
  await state.selectConversation(conversation('recent'))
  state.draft.value = 'New question'
  const sending = state.send()
  await flush()
  assert.equal(submitted, true)
  await state.loadOlderMessages()
  assert.deepEqual(
    state.messages.value.map((row) => row.id),
    ['old-message', 'recent-message']
  )
  release(response(latest, 'older-messages'))
  await sending
  assert.deepEqual(
    state.messages.value.map((row) => row.id),
    ['old-message', 'recent-message', 'new-message']
  )
  assert.equal(state.olderCursor.value, null)
})
