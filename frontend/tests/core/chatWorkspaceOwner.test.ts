import type { RunSnapshot } from '../../src/shared/stream.ts'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'

const originalFetch = globalThis.fetch
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
const { restoreIdentity, clearIdentity, identity } = await import(
  '../../src/modules/identity/public.ts'
)
const { provideChatWorkspace, useChatWorkspace } = await import(
  '../../src/modules/chat/composables/workspace.ts'
)
Object.defineProperty(globalThis, 'document', {
  configurable: true,
  value: Object.assign(new EventTarget(), { visibilityState: 'visible' })
})
after(() => {
  globalThis.fetch = originalFetch
  if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument)
  else Reflect.deleteProperty(globalThis, 'document')
})
const stamp = '2026-11-16T00:00:00Z'
const conversation = {
  id: 'private-A',
  title: 'A private conversation',
  created_at: stamp,
  version: 1,
  archived: false
}
const response = (value: unknown) =>
  new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
const run: RunSnapshot = {
  id: 'run-A',
  resource_type: 'chat',
  resource_id: conversation.id,
  status: 'completed',
  attempt_id: null,
  attempt_no: 0,
  revision: 1,
  projection_seq: 1,
  text: 'A late answer',
  error: null,
  created_at: stamp,
  usage: { input_tokens: 1, output_tokens: 1, status: 'known' }
}
class Fixture {
  owner = 'owner-A'
  permissions = ['platform:chat:enabled', 'platform:chat:read', 'platform:chat:use']
  metadata = { ...conversation }
  metadataFailure = false
  modelReads = 0
  writes: string[] = []
  history: Record<string, unknown>[] = [
    {
      id: 'secret-A',
      role: 'assistant',
      content: 'A PRIVATE ANSWER',
      run_id: 'old-run-A',
      created_at: stamp,
      is_complete: true
    }
  ]
  state!: ReturnType<typeof useChatWorkspace>
  admissions: { actor: string; body: { message: string; client_request_id: string } }[] = []
  admissionReplies: Response[] = []
  regenerations: { path: string; body: { client_request_id: string } }[] = []
  regenerationReplies: Response[] = []
  upload = deferred<Response>()
  admission = deferred<Response>()
  holdAdmission = false
  admissionSignal: AbortSignal | null | undefined
  constructor() {
    fetcher = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, 'http://fixture')
      if (init?.method && init.method !== 'GET') this.writes.push(url.pathname)
      if (url.pathname === '/api/auth/me')
        return response({
          id: this.owner,
          username: this.owner,
          is_admin: false,
          permissions: this.permissions
        })
      if (url.pathname === '/api/models') {
        this.modelReads++
        return response([
          {
            id: 'model-1',
            name: 'Model',
            connection_id: 'connection-1',
            upstream_model: 'fixture',
            max_output_tokens: 100,
            model_type: 'chat',
            enabled: true,
            published: true,
            capabilities: ['chat', 'stream']
          }
        ])
      }
      if (url.pathname === '/api/chat/conversations')
        return response(this.owner === 'owner-A' ? [conversation] : [])
      if (
        url.pathname === '/api/chat/conversations/' + conversation.id &&
        (!init?.method || init.method === 'GET')
      )
        return this.metadataFailure
          ? new Response(
              JSON.stringify({
                error: { code: 'unavailable', message: 'metadata temporarily unavailable' }
              }),
              { status: 503 }
            )
          : response(this.metadata)
      if (url.pathname.endsWith('/messages')) return response(this.history)
      if (url.pathname.endsWith('/context'))
        return response({
          status: 'full',
          strategy: 'token',
          effective_memory: {
            summary_used: false,
            covered_rounds: 0,
            retained_rounds: 1,
            estimated_tokens: null
          },
          last_run_context: null,
          reason: null
        })
      if (url.pathname === '/api/chat/attachments') return this.upload.promise
      if (url.pathname === '/api/runs/run-A/cancel')
        return response({ ...run, status: 'cancelled', revision: 2, projection_seq: 2 })
      if (url.pathname.endsWith('/regenerate')) {
        assert.ok(typeof init?.body === 'string')
        this.regenerations.push({
          path: url.pathname,
          body: JSON.parse(init.body) as { client_request_id: string }
        })
        const reply = this.regenerationReplies.shift()
        assert.ok(reply, 'regeneration fixture requires an explicit response')
        return reply
      }
      if (url.pathname.endsWith('/runs')) {
        assert.ok(typeof init?.body === 'string')
        this.admissions.push({
          actor: this.owner,
          body: JSON.parse(init.body) as { message: string; client_request_id: string }
        })
        const reply = this.admissionReplies.shift()
        if (reply) return reply
        this.admissionSignal = init?.signal
        if (this.holdAdmission) return this.admission.promise
        throw new TypeError('admission response lost')
      }
      throw new Error('Unexpected fixture request: ' + url.pathname)
    }
  }
  async mount(t: TestContext) {
    await restoreIdentity(true)
    const probe = {
      setup: () => {
        this.state = useChatWorkspace()
        return () => h(RouterView)
      }
    }
    const provider = {
      setup() {
        provideChatWorkspace()
        return () => h(probe)
      }
    }
    const root = {
      setup() {
        return () => (identity.value ? h(provider, { key: identity.value.id }) : null)
      }
    }
    const page = { render: () => null }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/chat', name: 'chat', component: page },
        { path: '/models', name: 'models', component: page }
      ]
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
    const app = renderer.createApp(root)
    app.use(router)
    app.mount({})
    t.after(async () => {
      this.upload.resolve(response({}))
      this.admission.resolve(response(run))
      await flush()
      app.unmount()
      clearIdentity()
    })
    await flush()
    return router
  }
  async switchOwner() {
    this.owner = 'owner-B'
    // The foreground tab's focus handler refreshes /auth/me after another tab logs in.
    await restoreIdentity(true)
    await flush()
  }
}

void test('same-permission owner replacement remounts private chat state and revokes old retry closures', async (t) => {
  const appSource = await readFile(new URL('../../src/app/App.vue', import.meta.url), 'utf8')
  assert.match(appSource, /<ChatWorkspaceProvider[^>]*:key="identity\.id"/)
  const fixture = new Fixture()
  await fixture.mount(t)
  const old = fixture.state
  await old.selectConversation(conversation)
  old.draft.value = 'A PRIVATE PENDING MESSAGE'
  await old.send()
  assert.equal(old.canRetry.value, true)
  old.addFiles([new File(['private bytes'], 'A-private.txt', { type: 'text/plain' })])
  await flush()
  assert.equal(old.queuedFiles.value[0]?.fileName, 'A-private.txt')
  await fixture.switchOwner()
  const current = fixture.state
  assert.notEqual(current, old)
  assert.equal(identity.value?.id, 'owner-B')
  assert.equal(current.selected.value, null)
  assert.deepEqual(current.messages.value, [])
  assert.equal(current.draft.value, '')
  assert.deepEqual(current.queuedFiles.value, [])
  assert.deepEqual(current.selectedSkills.value, [])
  assert.equal(current.canRetry.value, false)
  assert.equal(old.canRetry.value, false)
  await old.send(true)
  old.draft.value = 'old closure must not submit this'
  await old.send()
  assert.equal(fixture.admissions.length, 1)
  assert.equal(fixture.admissions[0]?.actor, 'owner-A')
})

void test('same-permission owner replacement aborts a pending admission and ignores its late body', async (t) => {
  const fixture = new Fixture()
  fixture.holdAdmission = true
  await fixture.mount(t)
  const old = fixture.state
  await old.selectConversation(conversation)
  old.draft.value = 'A private request'
  const sending = old.send()
  await flush()
  assert.equal(old.sending.value, true)
  await fixture.switchOwner()
  assert.equal(fixture.admissionSignal?.aborted, true)
  fixture.admission.resolve(response(run))
  await sending
  assert.equal(fixture.state.selected.value, null)
  assert.deepEqual(fixture.state.messages.value, [])
  assert.equal(fixture.state.activeRun.value, null)
  assert.equal(fixture.state.sending.value, false)
  assert.equal(fixture.admissions.length, 1)
})

void test('a single owner keeps conversation, draft and attachment queue across ordinary route changes', async (t) => {
  const fixture = new Fixture()
  const router = await fixture.mount(t)
  const original = fixture.state
  await original.selectConversation(conversation)
  original.draft.value = 'same owner draft'
  original.addFiles([new File(['bytes'], 'same-owner.txt', { type: 'text/plain' })])
  await router.push('/models')
  await flush()
  await router.push('/chat')
  await flush()
  assert.equal(fixture.state, original)
  assert.equal(fixture.state.selected.value?.id, conversation.id)
  assert.equal(fixture.state.messages.value[0]?.content, 'A PRIVATE ANSWER')
  assert.equal(fixture.state.draft.value, 'same owner draft')
  assert.equal(fixture.state.queuedFiles.value[0]?.fileName, 'same-owner.txt')
})

for (const extraModelRead of [false, true]) {
  void test(`chat read-only history is usable without write controls (model read: ${extraModelRead})`, async (t) => {
    const fixture = new Fixture()
    fixture.permissions = [
      'platform:chat:enabled',
      'platform:chat:read',
      ...(extraModelRead ? ['platform:models:enabled', 'platform:models:read'] : [])
    ]
    fixture.history = [
      {
        id: 'question-A',
        role: 'user',
        content: 'A question',
        run_id: 'old-run-A',
        created_at: stamp,
        is_complete: true
      },
      { ...fixture.history[0], reply_to_message_id: 'question-A' }
    ]
    await fixture.mount(t)
    const state = fixture.state
    assert.equal(state.conversations.value[0]?.id, conversation.id)
    assert.equal(state.listError.value, '')
    assert.equal(fixture.modelReads, 0)
    assert.equal(state.canUseChat.value, false)
    await state.selectConversation(conversation)
    const answer = state.messages.value.find((message) => message.role === 'assistant')!
    assert.equal(answer.content, 'A PRIVATE ANSWER')
    state.setModelId('model-1')
    state.draft.value = 'must not send'
    await state.send()
    await state.regenerate(answer)
    state.addFiles([new File(['bytes'], 'must-not-upload.txt', { type: 'text/plain' })])
    assert.deepEqual(state.queuedFiles.value, [])
    assert.equal(await state.changeConversation(conversation, { title: 'not allowed' }), false)
    assert.equal(await state.changeConversation(conversation, 'delete'), false)
    assert.match(state.shareReason(answer), /分享权限/)
    assert.deepEqual(fixture.writes, [])
    state.activeRun.value = {
      ...run,
      status: 'running',
      usage: { input_tokens: null, output_tokens: null, status: 'pending' }
    }
    await state.stop()
    assert.equal(state.activeRun.value?.status, 'cancelled')
    assert.deepEqual(fixture.writes, ['/api/runs/run-A/cancel'])
  })
}

void test('an unknown chat admission keeps its UUID through an edited draft and preserves that draft after original success', async (t) => {
  const fixture = new Fixture()
  await fixture.mount(t)
  const state = fixture.state
  await state.selectConversation(conversation)
  state.draft.value = 'original question'
  await state.send()
  assert.equal(state.canRetry.value, true)
  state.draft.value = 'a new unsent draft'
  await state.send()
  assert.equal(fixture.admissions.length, 1)
  fixture.admissionReplies.push(response(run))
  await state.send(true)
  assert.equal(fixture.admissions.length, 2)
  assert.deepEqual(fixture.admissions[1]?.body, fixture.admissions[0]?.body)
  assert.equal(state.draft.value, 'a new unsent draft')
  assert.equal(state.canRetry.value, false)
  fixture.admissionReplies.push(response({ ...run, id: 'next-run' }))
  await state.send()
  assert.equal(fixture.admissions[2]?.body.message, 'a new unsent draft')
  assert.notEqual(
    fixture.admissions[2]?.body.client_request_id,
    fixture.admissions[0]?.body.client_request_id
  )
})

void test('a definitive chat 404 releases the rejected intent so a corrected payload uses a new UUID', async (t) => {
  const fixture = new Fixture()
  fixture.admissionReplies.push(
    new Response(
      JSON.stringify({ error: { code: 'model_not_found', message: 'model unavailable' } }),
      { status: 404 }
    )
  )
  await fixture.mount(t)
  const state = fixture.state
  await state.selectConversation(conversation)
  state.draft.value = 'rejected request'
  await state.send()
  assert.equal(state.canRetry.value, false)
  fixture.admissionReplies.push(response(run))
  state.draft.value = 'corrected request'
  await state.send()
  assert.equal(fixture.admissions.length, 2)
  assert.notEqual(
    fixture.admissions[1]?.body.client_request_id,
    fixture.admissions[0]?.body.client_request_id
  )
  assert.equal(fixture.admissions[1]?.body.message, 'corrected request')
  assert.equal(state.draft.value, '')
})

for (const status of [408, 503]) {
  void test(`chat HTTP ${status} keeps an unknown admission for exact retry`, async (t) => {
    const fixture = new Fixture()
    fixture.admissionReplies.push(
      new Response(
        JSON.stringify({
          error: {
            code: status === 408 ? 'context_input_too_long' : 'unavailable',
            message: 'response uncertain'
          }
        }),
        { status }
      )
    )
    await fixture.mount(t)
    const state = fixture.state
    await state.selectConversation(conversation)
    state.draft.value = 'original payload'
    await state.send()
    assert.equal(state.canRetry.value, true)
    state.draft.value = 'changed payload'
    await state.send()
    assert.equal(fixture.admissions.length, 1)
    fixture.admissionReplies.push(response(run))
    await state.send(true)
    assert.deepEqual(fixture.admissions[1]?.body, fixture.admissions[0]?.body)
    assert.equal(state.draft.value, 'changed payload')
  })
}

void test('unknown chat intent survives read-only navigation and resumes under its original conversation after use returns', async (t) => {
  const fixture = new Fixture()
  await fixture.mount(t)
  const state = fixture.state
  await state.selectConversation(conversation)
  state.draft.value = 'A original unknown question'
  await state.send()
  const original = fixture.admissions[0]!.body
  fixture.permissions = ['platform:chat:enabled', 'platform:chat:read']
  await restoreIdentity(true)
  await flush()
  fixture.history = [
    {
      id: 'B-history',
      role: 'assistant',
      content: 'Other readable history',
      run_id: 'old-B',
      created_at: stamp,
      is_complete: true
    }
  ]
  const other = { ...conversation, id: 'other-owned', title: 'Other owned conversation' }
  await state.selectConversation(other)
  assert.equal(state.selected.value?.id, other.id)
  assert.equal(state.messages.value[0]?.content, 'Other readable history')
  assert.equal(state.canRetry.value, false)
  assert.equal(state.conversationLocked(conversation.id), true)
  assert.equal(await state.changeConversation(conversation, 'delete'), false)
  await state.send(true)
  assert.equal(fixture.admissions.length, 1)
  fixture.permissions.push('platform:chat:use', 'platform:chat:update', 'platform:chat:delete')
  await restoreIdentity(true)
  await flush()
  const writesBefore = fixture.writes.length
  assert.equal(state.conversationLocked(conversation.id), true)
  assert.equal(state.conversationLocked(other.id), false)
  assert.equal(await state.changeConversation(conversation, 'delete'), false)
  assert.equal(fixture.writes.length, writesBefore)
  state.draft.value = 'B independent question'
  fixture.admissionReplies.push(response({ ...run, id: 'run-B', resource_id: other.id }))
  await state.send()
  assert.equal(fixture.admissions.length, 2)
  assert.notEqual(fixture.admissions[1]?.body.client_request_id, original.client_request_id)
  await state.selectConversation(conversation)
  assert.equal(state.canRetry.value, true)
  fixture.admissionReplies.push(response(run))
  await state.send(true)
  assert.equal(fixture.admissions.length, 3)
  assert.deepEqual(fixture.admissions[2]?.body, original)
})

void test('chat regeneration HTTP 408 preserves its original message and request UUID until confirmation', async (t) => {
  const fixture = new Fixture()
  fixture.history = [
    {
      id: 'question-A',
      role: 'user',
      content: 'Original question',
      run_id: null,
      created_at: stamp,
      is_complete: true
    },
    {
      id: 'answer-A',
      role: 'assistant',
      content: 'Original answer',
      run_id: 'old-run-A',
      reply_to_message_id: 'question-A',
      generation_index: 1,
      created_at: stamp,
      is_complete: true
    },
    {
      id: 'answer-A-v2',
      role: 'assistant',
      content: 'Alternative answer',
      run_id: 'old-run-A-v2',
      reply_to_message_id: 'question-A',
      generation_index: 2,
      created_at: stamp,
      is_complete: true
    }
  ]
  fixture.regenerationReplies.push(
    new Response(JSON.stringify({ error: { code: 'timeout', message: 'admission uncertain' } }), {
      status: 408
    })
  )
  await fixture.mount(t)
  const state = fixture.state
  await state.selectConversation(conversation)
  assert.equal(state.historyReady.value, true)
  const originalAnswer = state.messages.value.find((message) => message.id === 'answer-A')
  const otherAnswer = state.messages.value.find((message) => message.id === 'answer-A-v2')
  assert.ok(originalAnswer)
  assert.ok(otherAnswer)
  await state.regenerate(originalAnswer)
  assert.equal(state.regenerationPending.value, true)
  assert.equal(state.canRetryRegeneration.value, true)
  await state.regenerate(otherAnswer)
  assert.equal(fixture.regenerations.length, 1)
  fixture.regenerationReplies.push(
    new Response(JSON.stringify({ error: { code: 'forbidden', message: 'temporarily denied' } }), {
      status: 403
    })
  )
  await state.retryRegeneration()
  assert.equal(fixture.regenerations.length, 2)
  assert.equal(state.regenerationPending.value, true)
  fixture.regenerationReplies.push(
    new Response(
      JSON.stringify({ error: { code: 'invalid_config', message: 'configuration changed' } }),
      { status: 422 }
    )
  )
  await state.retryRegeneration()
  assert.equal(fixture.regenerations.length, 3)
  assert.equal(state.regenerationPending.value, true)
  fixture.regenerationReplies.push(response(run))
  await state.retryRegeneration()
  assert.equal(fixture.regenerations.length, 4)
  assert.deepEqual(fixture.regenerations[1], fixture.regenerations[0])
  assert.deepEqual(fixture.regenerations[2], fixture.regenerations[0])
  assert.deepEqual(fixture.regenerations[3], fixture.regenerations[0])
  assert.equal(
    fixture.regenerations[0]?.path,
    '/api/chat/conversations/private-A/messages/answer-A/regenerate'
  )
  assert.ok(fixture.regenerations[0]?.body.client_request_id)
  assert.equal(state.regenerationPending.value, false)
})

for (const status of [403, 422]) {
  void test(`a rejected HTTP ${status} confirmation cannot erase an earlier unknown chat admission for the same owner`, async (t) => {
    const fixture = new Fixture()
    await fixture.mount(t)
    const state = fixture.state
    await state.selectConversation(conversation)
    state.draft.value = 'original possibly accepted question'
    await state.send()
    const original = fixture.admissions[0]!.body
    fixture.admissionReplies.push(
      new Response(
        JSON.stringify({
          error: {
            code: status === 422 ? 'context_input_too_long' : 'forbidden',
            message: 'confirmation rejected'
          }
        }),
        {
          status
        }
      )
    )
    await state.send(true)
    assert.equal(fixture.admissions.length, 2)
    assert.equal(state.canRetry.value, true)
    fixture.permissions = fixture.permissions.filter((code) => code !== 'platform:chat:use')
    await restoreIdentity(true)
    await flush()
    await state.send(true)
    assert.equal(fixture.admissions.length, 2)
    fixture.permissions.push('platform:chat:use')
    await restoreIdentity(true)
    await flush()
    state.draft.value = 'new unsent question'
    fixture.admissionReplies.push(response(run))
    await state.send(true)
    assert.equal(fixture.admissions.length, 3)
    assert.deepEqual(fixture.admissions[1]?.body, original)
    assert.deepEqual(fixture.admissions[2]?.body, original)
    assert.equal(state.draft.value, 'new unsent question')
  })
}

void test('completed chat synchronizes the generated title in the selected conversation and history without manual refresh', async (t) => {
  const fixture = new Fixture()
  await fixture.mount(t)
  const state = fixture.state
  await state.selectConversation({ ...conversation, title: '新对话' })
  fixture.metadata = { ...conversation, title: '自动同步后的标题' }
  fixture.admissionReplies.push(response(run))
  state.draft.value = 'A successful question'
  await state.send()
  await flush()
  assert.equal(state.selected.value?.title, '自动同步后的标题')
  assert.equal(
    state.conversations.value.find((item) => item.id === conversation.id)?.title,
    '自动同步后的标题'
  )
})
void test('a metadata refresh failure preserves the confirmed successful run', async (t) => {
  const fixture = new Fixture()
  await fixture.mount(t)
  const state = fixture.state
  await state.selectConversation(conversation)
  fixture.metadataFailure = true
  fixture.admissionReplies.push(response(run))
  state.draft.value = 'A successful question'
  await state.send()
  await flush()
  assert.equal(state.activeRun.value?.status, 'completed')
  assert.equal(state.error.value, '')
})
