import { grantedPermissions } from './permissionFixture.ts'
import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, reactive } from 'vue'
import type { ChatModel, ModelConnection } from '../../src/modules/models/api/index.ts'
import type { RunSnapshot } from '../../src/shared/stream.ts'

const originalFetch = globalThis.fetch
const originalStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
  if (originalStorage) Object.defineProperty(globalThis, 'sessionStorage', originalStorage)
  else Reflect.deleteProperty(globalThis, 'sessionStorage')
})
const { useModelAccess } = await import('../../src/modules/models/composables/useModelAccess.ts')
const { useModelCatalog } = await import('../../src/modules/models/composables/useModelCatalog.ts')
const { useModelConfiguration } = await import(
  '../../src/modules/models/composables/useModelConfiguration.ts'
)
const { useConnectionConfiguration } = await import(
  '../../src/modules/models/composables/useConnectionConfiguration.ts'
)
const { useModelTrial } = await import('../../src/modules/models/composables/useModelTrial.ts')
const { restoreIdentity, clearIdentity } = await import('../../src/modules/identity/public.ts')
const model: ChatModel = {
  id: '87c1558e-7b8b-46a3-8e1b-d06eea1c53c5',
  name: 'Original model',
  connection_id: 'connection-1',
  upstream_model: 'provider/model',
  max_output_tokens: 512,
  enabled: true,
  model_type: 'chat',
  vision_enabled: false,
  capabilities: ['chat', 'stream']
}
const connection: ModelConnection = {
  id: 'connection-1',
  name: 'Connection',
  provider: 'openai_compatible',
  base_url: 'https://provider.example/v1',
  has_api_key: true,
  enabled: true
}
const snapshot = (changes: Partial<RunSnapshot> = {}): RunSnapshot => ({
  id: '7265f4f1-107d-4fb4-afd9-824c60c7f290',
  resource_type: 'connection_test',
  resource_id: model.id,
  status: 'pending',
  attempt_id: 'attempt-1',
  attempt_no: 1,
  revision: 1,
  projection_seq: 0,
  text: '',
  error: null,
  created_at: '2026-11-15T00:00:00Z',
  usage: { input_tokens: null, output_tokens: null, status: 'pending' },
  ...changes
})
const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' }
  })
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
class Fixture {
  unmount = () => {}
  calls: { path: string; method: string; body: unknown }[] = []
  override?: (url: URL, init?: RequestInit) => Response | Promise<Response | undefined> | undefined
  constructor() {
    const storage = new Map<string, string>()
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
        removeItem: (key: string) => storage.delete(key)
      }
    })
    fetcher = async (input, init) => {
      const url = new URL(input instanceof Request ? input.url : input, 'http://fixture')
      this.calls.push({
        path: url.pathname,
        method: init?.method ?? 'GET',
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : null
      })
      const custom = await this.override?.(url, init)
      if (custom) return custom
      if (url.pathname === '/api/auth/me')
        return json({ id: 'user-1', username: 'test', is_admin: true })
      if (url.pathname === '/api/models/connections')
        return json(init?.method === 'POST' ? connection : [connection])
      if (url.pathname === '/api/models/connection-options') return json([connection])
      if (url.pathname === '/api/models') return json([model])
      throw new Error('Unexpected request: ' + url.toString())
    }
  }
  async mount<T>(t: TestContext, setup: () => T): Promise<T> {
    await restoreIdentity(true)
    let result!: T
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
        result = setup()
        return () => null
      }
    })
    app.mount({})
    let mounted = true
    const unmount = () => {
      if (!mounted) return
      mounted = false
      app.unmount()
    }
    this.unmount = unmount
    t.after(() => {
      unmount()
      clearIdentity()
    })
    await flush()
    return result
  }
}

void test('model capacity validation belongs to model configuration and leaves credentials independent', async (t) => {
  const fixture = new Fixture()
  const { models, credentials } = await fixture.mount(t, () => {
    const access = useModelAccess()
    return {
      models: useModelConfiguration({
        access,
        connections: () => [connection],
        pending: {},
        saved() {}
      }),
      credentials: useConnectionConfiguration({ access, saved() {} })
    }
  })
  models.openModel()
  Object.assign(models.modelForm, {
    name: 'Model',
    upstream_model: 'provider/model',
    capacity_note: 'bad\0note'
  })
  await models.saveModel()
  assert.match(models.modalError.value, /容量依据/)
  assert.equal(fixture.calls.filter((call) => call.method === 'POST').length, 0)
  credentials.openConnection()
  Object.assign(credentials.connectionForm, {
    name: 'Valid connection',
    base_url: connection.base_url,
    api_key: 'fixture-key'
  })
  await credentials.saveConnection()
  assert.equal(credentials.modalError.value, '')
  assert.equal(credentials.connectionOpen.value, false)
  assert.equal(credentials.connectionForm.api_key, '')
  assert.match(models.modalError.value, /容量依据/)
  assert.deepEqual(fixture.calls.find((call) => call.method === 'POST')?.body, {
    name: 'Valid connection',
    base_url: connection.base_url,
    api_key: 'fixture-key'
  })
})

void test('a superseded credential save cannot erase the new form secret or publish its result', async (t) => {
  const fixture = new Fixture()
  const reply = deferred<Response>()
  const started = deferred<void>()
  fixture.override = (url, init) => {
    if (url.pathname === '/api/models/connections' && init?.method === 'POST') {
      started.resolve(undefined)
      return reply.promise
    }
  }
  const saved: ModelConnection[] = []
  const state = await fixture.mount(t, () =>
    useConnectionConfiguration({
      access: useModelAccess(),
      saved: (value) => {
        saved.push(value)
      }
    })
  )
  state.openConnection()
  Object.assign(state.connectionForm, {
    name: 'Old',
    base_url: connection.base_url,
    api_key: 'old-key'
  })
  const saving = state.saveConnection()
  await started.promise
  state.closeConnection()
  state.openConnection()
  Object.assign(state.connectionForm, {
    name: 'New',
    base_url: connection.base_url,
    api_key: 'new-key'
  })
  reply.resolve(json(connection))
  await saving
  assert.equal(state.connectionForm.api_key, 'new-key')
  assert.equal(state.connectionForm.name, 'New')
  assert.equal(state.connectionOpen.value, true)
  assert.equal(state.saving.value, false)
  assert.deepEqual(saved, [])
})

void test('a model save invalidates an old directory response and finishes the interrupted refresh', async (t) => {
  const fixture = new Fixture()
  const stale = deferred<Response>()
  const requested = deferred<void>()
  let reads = 0
  const updated = { ...model, name: 'Confirmed model' }
  fixture.override = (url) => {
    if (url.pathname !== '/api/models') return
    reads += 1
    if (reads === 2) {
      requested.resolve(undefined)
      return stale.promise
    }
    return json([reads > 2 ? updated : model])
  }
  const catalog = await fixture.mount(t, () =>
    useModelCatalog(false, useModelAccess(), {
      busy: () => false,
      deleted() {}
    })
  )
  const loading = catalog.load()
  await requested.promise
  catalog.modelSaved(updated, true)
  await flush()
  stale.resolve(json([model]))
  await loading
  assert.equal(catalog.models.value[0]?.name, 'Confirmed model')
  assert.equal(catalog.loading.value, false)
  assert.equal(reads, 3)
})

void test('a late cancel response cannot restart observation after the trial completed', async (t) => {
  const fixture = new Fixture()
  const cancelReply = deferred<Response>()
  const cancelRequested = deferred<void>()
  const streamReady = deferred<ReadableStreamDefaultController<Uint8Array>>()
  let eventRequests = 0
  fixture.override = (url, init) => {
    if (url.pathname.endsWith('/trial') && init?.method === 'POST') return json(snapshot())
    if (url.pathname.endsWith('/events')) {
      eventRequests += 1
      return new Response(
        new ReadableStream<Uint8Array>({ start: (stream) => streamReady.resolve(stream) }),
        {
          headers: { 'Content-Type': 'text/event-stream' }
        }
      )
    }
    if (url.pathname.endsWith('/cancel')) {
      cancelRequested.resolve(undefined)
      return cancelReply.promise
    }
  }
  const state = await fixture.mount(t, () =>
    useModelTrial(
      reactive({
        models: [model],
        canTest: true,
        pending: {},
        connectionIssues: {}
      }),
      { refresh() {}, denied() {} }
    )
  )
  assert.equal(state.open(model), true)
  state.prompt.value = 'An independent trial'
  const submitting = state.submit()
  const stream = await streamReady.promise
  const stopping = state.stop()
  await cancelRequested.promise
  const completed = snapshot({
    status: 'completed',
    revision: 3,
    projection_seq: 1,
    text: 'Final answer'
  })
  stream.enqueue(
    new TextEncoder().encode('event: snapshot\ndata: ' + JSON.stringify(completed) + '\n\n')
  )
  stream.close()
  await submitting
  cancelReply.resolve(json(snapshot({ status: 'cancel_requested', revision: 2 })))
  await stopping
  assert.equal(state.run.value?.status, 'completed')
  assert.equal(state.run.value?.text, 'Final answer')
  assert.equal(state.working.value, false)
  assert.equal(state.pendingResult.value, false)
  assert.equal(eventRequests, 1)
})

void test('switching to an equally privileged owner clears model and credential context and rejects old saves', async (t) => {
  const fixture = new Fixture()
  const oldSave = deferred<Response>()
  const saveStarted = deferred<void>()
  const nextCatalog = deferred<Response>()
  let owner = 'user-1'
  fixture.override = (url, init) => {
    if (url.pathname === '/api/auth/me')
      return json({ id: owner, username: 'test', is_admin: true })
    if (url.pathname === '/api/models' && owner !== 'user-1') return nextCatalog.promise
    if (url.pathname === '/api/models/connections' && init?.method === 'POST') {
      saveStarted.resolve(undefined)
      return oldSave.promise
    }
  }
  const saved: ModelConnection[] = []
  const { catalog, models, credentials } = await fixture.mount(t, () => {
    const access = useModelAccess()
    const catalog = useModelCatalog(false, access, { busy: () => false, deleted() {} })
    return {
      catalog,
      models: useModelConfiguration({
        access,
        connections: () => [connection],
        pending: {},
        saved() {}
      }),
      credentials: useConnectionConfiguration({
        access,
        saved: (value) => {
          saved.push(value)
        }
      })
    }
  })
  models.openModel(model)
  credentials.openConnection()
  Object.assign(credentials.connectionForm, {
    name: 'Old owner',
    base_url: connection.base_url,
    api_key: 'old-owner-key'
  })
  const saving = credentials.saveConnection()
  await saveStarted.promise
  owner = 'user-2'
  await restoreIdentity(true)
  await flush()
  assert.equal(models.modelOpen.value, false)
  assert.equal(models.modelForm.upstream_model, '')
  assert.equal(credentials.connectionOpen.value, false)
  assert.deepEqual({ ...credentials.connectionForm }, { name: '', base_url: '', api_key: '' })
  assert.equal(catalog.models.value.length, 0)
  credentials.openConnection()
  credentials.connectionForm.api_key = 'new-owner-key'
  oldSave.resolve(json(connection))
  await saving
  assert.equal(credentials.connectionForm.api_key, 'new-owner-key')
  assert.deepEqual(saved, [])
  nextCatalog.resolve(json([{ ...model, name: 'New owner model' }]))
  await flush()
  assert.equal(catalog.models.value[0]?.name, 'New owner model')
})

void test('same-owner authority refresh preserves the pending trial intent while invalidating its submission', async (t) => {
  const fixture = new Fixture()
  const oldSubmission = deferred<Response>()
  const submitted = deferred<void>()
  let username = 'before-refresh'
  let posts = 0
  fixture.override = (url, init) => {
    if (url.pathname === '/api/auth/me') return json({ id: 'user-1', username, is_admin: true })
    if (url.pathname.endsWith('/trial') && init?.method === 'POST') {
      posts += 1
      if (posts === 1) {
        submitted.resolve(undefined)
        return oldSubmission.promise
      }
      return json(snapshot({ status: 'completed', revision: 3, text: 'Confirmed once' }))
    }
  }
  const state = await fixture.mount(t, () =>
    useModelTrial(
      reactive({
        models: [model],
        canTest: true,
        pending: {},
        connectionIssues: {}
      }),
      { refresh() {}, denied() {} }
    )
  )
  state.open(model)
  state.prompt.value = 'Original trial input'
  const saving = state.submit()
  await submitted.promise
  username = 'after-refresh'
  await restoreIdentity(true)
  await flush()
  assert.equal(state.uncertain.value, true)
  assert.equal(state.working.value, false)
  oldSubmission.resolve(json(snapshot()))
  await saving
  const interruptedRun = state.run.value
  assert.equal(interruptedRun, null)
  state.prompt.value = 'A later draft must not change the original intent'
  await state.submit()
  const bodies = fixture.calls
    .filter((call) => call.path.endsWith('/trial'))
    .map((call) => call.body)
  assert.equal(bodies.length, 2)
  assert.deepEqual(bodies[0], bodies[1])
  assert.equal(state.run.value?.status, 'completed')
  assert.equal(state.run.value?.text, 'Confirmed once')
})

for (const action of ['config_read', 'create', 'update', 'delete', 'toggle']) {
  void test(`model ${action} permits configuration queries without unrelated commands`, async (t) => {
    const fixture = new Fixture()
    fixture.override = (url) => {
      if (url.pathname === '/api/auth/me')
        return json({
          id: 'user-1',
          username: 'member',
          is_admin: false,
          permissions: grantedPermissions(`platform:models:${action}`)
        })
    }
    const { access, catalog, configuration } = await fixture.mount(t, () => {
      const access = useModelAccess()
      return {
        access,
        catalog: useModelCatalog(false, access, { busy: () => false, deleted() {} }),
        configuration: useModelConfiguration({
          access,
          connections: () => [connection],
          pending: {},
          saved() {}
        })
      }
    })
    assert.equal(access.canReadModels.value, true)
    assert.equal(access.canReadCredentials.value, false)
    assert.equal(access.canTestModels.value, false)
    assert.equal(access.canReadRates.value, false)
    assert.equal(access.canCreateRates.value, false)
    assert.ok(fixture.calls.some((call) => call.path === '/api/models/connection-options'))
    assert.equal(catalog.models.value[0]?.id, model.id)
    configuration.openModel()
    assert.equal(configuration.modelOpen.value, action === 'create')
    configuration.closeModel()
    configuration.openModel(model)
    assert.equal(configuration.modelOpen.value, action === 'update')
    assert.equal(
      fixture.calls.some((call) => call.method !== 'GET'),
      false
    )
  })
}

void test('model operation checks stop using grants when read or the module gate is revoked', async (t) => {
  const fixture = new Fixture()
  let permissions = grantedPermissions(
    'platform:models:create',
    'ai:credentials:create',
    'ai:rates:create'
  )
  fixture.override = (url) => {
    if (url.pathname === '/api/auth/me')
      return json({ id: 'user-1', username: 'member', is_admin: false, permissions })
  }
  const access = await fixture.mount(t, () => useModelAccess())
  assert.equal(access.canCreateModels.value, true)
  assert.equal(access.canCreateCredentials.value, true)
  assert.equal(access.canCreateRates.value, true)
  permissions = permissions.filter((code) => !code.endsWith(':read'))
  await restoreIdentity(true)
  assert.equal(access.canReadModels.value, false)
  assert.equal(access.canCreateModels.value, false)
  assert.equal(access.canReadCredentials.value, false)
  assert.equal(access.canCreateCredentials.value, false)
  assert.equal(access.canCreateRates.value, false)
  permissions = grantedPermissions(
    'platform:models:create',
    'ai:credentials:create',
    'ai:rates:create'
  ).filter((code) => !code.endsWith(':enabled'))
  await restoreIdentity(true)
  assert.equal(access.canReadModels.value, false)
  assert.equal(access.canCreateModels.value, false)
  assert.equal(access.canCreateCredentials.value, false)
  assert.equal(access.canCreateRates.value, false)
})

const rerankTrialModel: ChatModel = { ...model, model_type: 'rerank', capabilities: ['rerank'] }
const otherTrialModel: ChatModel = { ...model, id: '102f9006-b1ce-4afb-a330-70e983d19bed' }
const trialOptions = () =>
  reactive({
    models: [rerankTrialModel, otherTrialModel],
    canTest: true,
    pending: {},
    connectionIssues: {}
  })
const persistedTrialIntent = (raw: string): unknown => {
  const value: unknown = JSON.parse(raw)
  assert.ok(value && typeof value === 'object' && 'intent' in value)
  return value.intent
}

const recordedTrials = (fixture: Fixture) =>
  fixture.calls.filter((call) => call.path.endsWith('/trial'))

void test('FE-007 trial HTTP 408 replays the original model prompt documents and UUID', async (t) => {
  const { message } = await import('ant-design-vue')
  t.mock.method(message, 'info', () => undefined)
  const fixture = new Fixture()
  let posts = 0
  fixture.override = (url, init) => {
    if (!url.pathname.endsWith('/trial') || init?.method !== 'POST') return
    posts++
    return posts === 1
      ? new Response(
          JSON.stringify({ error: { code: 'timeout', message: 'Admission unconfirmed' } }),
          { status: 408 }
        )
      : json(snapshot({ status: 'completed', revision: 3, text: 'Confirmed original ranking' }))
  }
  const state = await fixture.mount(t, () =>
    useModelTrial(trialOptions(), { refresh() {}, denied() {} })
  )
  assert.equal(state.open(rerankTrialModel), true)
  state.prompt.value = 'Original ranking query'
  state.documents.value = 'Original document one\nOriginal document two'
  await state.submit()
  const afterTimeout = { uncertain: state.uncertain.value, pending: state.pendingResult.value }
  const saved = sessionStorage.getItem('zhiheng:model-trial:user-1:' + model.id)
  const switched = state.open(otherTrialModel)
  assert.equal(state.open(rerankTrialModel), true)
  state.prompt.value = 'A later draft must never replace the accepted request'
  state.documents.value = 'A different document'
  await state.submit()
  const calls = recordedTrials(fixture)
  assert.equal(calls.length, 2)
  assert.deepEqual(calls[1], calls[0])
  assert.deepEqual(afterTimeout, { uncertain: true, pending: true })
  assert.equal(switched, false)
  assert.ok(saved)
  const original = calls[0]!.body as {
    prompt: string
    documents: string[]
    client_request_id: string
  }
  assert.equal(calls[0]!.path, '/api/models/' + model.id + '/trial')
  assert.equal(original.prompt, 'Original ranking query')
  assert.deepEqual(original.documents, ['Original document one', 'Original document two'])
  assert.ok(original.client_request_id)
  assert.deepEqual(persistedTrialIntent(saved), original)
  assert.equal(state.run.value?.status, 'completed')
  assert.deepEqual(state.sentDocuments.value, original.documents)
  assert.equal(state.uncertain.value, false)
  assert.equal(sessionStorage.getItem('zhiheng:model-trial:user-1:' + model.id), null)
})

void test('FE-007 unknown trial survives same-owner permission loss and a forbidden confirmation', async (t) => {
  const fixture = new Fixture()
  const props = trialOptions()
  let posts = 0
  let denials = 0
  fixture.override = (url, init) => {
    if (!url.pathname.endsWith('/trial') || init?.method !== 'POST') return
    posts++
    if (posts === 1)
      return new Response(
        JSON.stringify({ error: { code: 'unavailable', message: 'Original result lost' } }),
        { status: 503 }
      )
    if (posts === 2)
      return new Response(
        JSON.stringify({
          error: { code: 'forbidden', message: 'Test permission temporarily revoked' }
        }),
        { status: 403 }
      )
    return json(snapshot({ status: 'completed', revision: 3, text: 'Original trial confirmed' }))
  }
  const state = await fixture.mount(t, () =>
    useModelTrial(props, {
      refresh() {},
      denied() {
        denials++
        props.canTest = false
      }
    })
  )
  assert.equal(state.open(rerankTrialModel), true)
  state.prompt.value = 'Original private ranking query'
  state.documents.value = 'First original source\nSecond original source'
  await state.submit()
  assert.equal(state.uncertain.value, true)
  const storageKey = 'zhiheng:model-trial:user-1:' + model.id
  const original = recordedTrials(fixture)[0]!.body
  props.canTest = false
  props.models = []
  await flush()
  assert.equal(state.visible.value, false)
  assert.equal(state.prompt.value, '')
  assert.equal(state.documents.value, '')
  const afterRevocation = sessionStorage.getItem(storageKey)
  assert.ok(afterRevocation)
  assert.deepEqual(persistedTrialIntent(afterRevocation), original)
  await state.submit()
  assert.equal(recordedTrials(fixture).length, 1)
  props.models = [rerankTrialModel, otherTrialModel]
  props.canTest = true
  await flush()
  assert.equal(state.open(rerankTrialModel), true)
  assert.equal(state.uncertain.value, true)
  state.prompt.value = 'A later draft before confirmation'
  state.documents.value = 'A later document'
  await state.submit()
  assert.equal(denials, 1)
  assert.equal(state.visible.value, false)
  assert.equal(state.prompt.value, '')
  assert.equal(state.documents.value, '')
  const afterForbiddenConfirmation = sessionStorage.getItem(storageKey)
  assert.ok(afterForbiddenConfirmation)
  assert.deepEqual(persistedTrialIntent(afterForbiddenConfirmation), original)
  await state.submit()
  assert.equal(recordedTrials(fixture).length, 2)
  props.canTest = true
  await flush()
  assert.equal(state.open(rerankTrialModel), true)
  await state.submit()
  assert.equal(recordedTrials(fixture).length, 3)
  assert.deepEqual(
    recordedTrials(fixture).map((call) => call.body),
    [original, original, original]
  )
  assert.equal(state.run.value?.status, 'completed')
  assert.equal(sessionStorage.getItem(storageKey), null)
})

void test('FE-007 an initial definitive trial rejection still permits a corrected fresh UUID', async (t) => {
  const fixture = new Fixture()
  let posts = 0
  fixture.override = (url, init) => {
    if (!url.pathname.endsWith('/trial') || init?.method !== 'POST') return
    posts++
    return posts === 1
      ? new Response(
          JSON.stringify({ error: { code: 'model_not_found', message: 'Model unavailable' } }),
          { status: 404 }
        )
      : json(snapshot({ status: 'completed', revision: 3, text: 'Corrected ranking' }))
  }
  const state = await fixture.mount(t, () =>
    useModelTrial(trialOptions(), { refresh() {}, denied() {} })
  )
  assert.equal(state.open(rerankTrialModel), true)
  state.prompt.value = 'Rejected query'
  state.documents.value = 'Rejected document'
  await state.submit()
  assert.equal(state.uncertain.value, false)
  assert.equal(sessionStorage.getItem('zhiheng:model-trial:user-1:' + model.id), null)
  state.prompt.value = 'Corrected query'
  state.documents.value = 'Corrected source one\nCorrected source two'
  await state.submit()
  const calls = recordedTrials(fixture)
  assert.equal(calls.length, 2)
  const first = calls[0]!.body as { client_request_id: string }
  const corrected = calls[1]!.body as {
    prompt: string
    documents: string[]
    client_request_id: string
  }
  assert.notEqual(corrected.client_request_id, first.client_request_id)
  assert.equal(corrected.prompt, 'Corrected query')
  assert.deepEqual(corrected.documents, ['Corrected source one', 'Corrected source two'])
  assert.equal(state.run.value?.status, 'completed')
})

for (const status of [404, 409, 422]) {
  void test(
    'FE-007 an unknown trial confirmation HTTP ' + status + ' never discards its original intent',
    async (t) => {
      const fixture = new Fixture()
      let posts = 0
      fixture.override = (url, init) => {
        if (!url.pathname.endsWith('/trial') || init?.method !== 'POST') return
        posts++
        if (posts === 1)
          return new Response(
            JSON.stringify({
              error: { code: 'timeout', message: 'Original admission unconfirmed' }
            }),
            { status: 408 }
          )
        if (posts === 2)
          return new Response(
            JSON.stringify({ error: { code: 'rejected', message: 'Confirmation rejected' } }),
            { status }
          )
        return json(
          snapshot({ status: 'completed', revision: 3, text: 'Original intent confirmed' })
        )
      }
      const props = trialOptions()
      const state = await fixture.mount(t, () =>
        useModelTrial(props, { refresh() {}, denied() {} })
      )
      assert.equal(state.open(rerankTrialModel), true)
      state.prompt.value = 'Frozen original query'
      state.documents.value = 'Frozen original source'
      await state.submit()
      assert.equal(state.uncertain.value, true)
      assert.equal(state.working.value, false)
      assert.equal(props.canTest, true)
      state.prompt.value = 'Changed local query'
      state.documents.value = 'Changed local source'
      await state.submit()
      assert.equal(state.uncertain.value, true)
      assert.equal(state.working.value, false)
      assert.equal(props.canTest, true)
      assert.equal(state.open(rerankTrialModel), true)
      await state.submit()
      const calls = recordedTrials(fixture)
      assert.equal(calls.length, 3)
      assert.deepEqual(calls[1], calls[0])
      assert.deepEqual(calls[2], calls[0])
      assert.equal(state.run.value?.status, 'completed')
    }
  )
}

void test('FE-007-disposed old trial closures cannot submit private inputs under a replacement owner', async (t) => {
  const fixture = new Fixture()
  let owner = 'user-1'
  fixture.override = (url, init) => {
    if (url.pathname === '/api/auth/me') return json({ id: owner, username: owner, is_admin: true })
    if (url.pathname.endsWith('/trial') && init?.method === 'POST')
      return new Response(
        JSON.stringify({ error: { code: 'timeout', message: 'Original admission uncertain' } }),
        { status: 408 }
      )
  }
  const state = await fixture.mount(t, () =>
    useModelTrial(trialOptions(), { refresh() {}, denied() {} })
  )
  assert.equal(state.open(rerankTrialModel), true)
  state.prompt.value = 'Original owner private input'
  state.documents.value = 'Original owner private document'
  await state.submit()
  assert.equal(recordedTrials(fixture).length, 1)
  fixture.unmount()
  owner = 'replacement-user'
  await restoreIdentity(true)
  await flush()
  await state.submit()
  assert.equal(recordedTrials(fixture).length, 1)
  assert.equal(state.open(otherTrialModel), false)
  assert.equal(state.run.value, null)
  assert.equal(state.prompt.value, '')
  assert.equal(state.documents.value, '')
})
