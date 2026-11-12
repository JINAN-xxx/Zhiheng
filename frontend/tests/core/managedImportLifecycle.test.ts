import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { effectScope, ref } from 'vue'
import type { KnowledgeBase, KnowledgeDocument } from '../../src/modules/knowledge/api/index.ts'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const { useManagedImport } = await import(
  '../../src/modules/knowledge/composables/useManagedImport.ts'
)
const { managedImportStorageKey } = await import(
  '../../src/modules/knowledge/managedImportDraft.ts'
)
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
const base = (id = 'base-1') =>
  ({
    id,
    can_manage: true,
    chunk_size: 1000,
    chunk_overlap: 200
  }) as KnowledgeBase
const job = {
  id: 'job-original',
  base_id: 'base-1',
  operation: 'import',
  status: 'completed',
  phase: 'completed',
  version: 2,
  attempt_id: 'attempt-1',
  index_revision: 'revision-1',
  total_documents: 1,
  completed_documents: 1,
  failed_documents: 0,
  chunk_count: 1,
  document_results: [],
  error: null,
  created_at: '2026-11-04T00:00:00Z',
  updated_at: '2026-11-04T00:00:00Z',
  finished_at: '2026-11-04T00:00:00Z',
  cancel_allowed: false
}

function fixture(t: TestContext) {
  const stored = new Map<string, string>()
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, value),
      removeItem: (key: string) => stored.delete(key)
    }
  })
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous)
    else Reflect.deleteProperty(globalThis, 'sessionStorage')
  })
  const context = {
    baseId: ref('base-1'),
    userId: ref('user-1'),
    selected: ref<KnowledgeBase | null>(base()),
    documents: ref<KnowledgeDocument[]>([]),
    folderId: ref<string | null>('folder-original'),
    actionBusy: ref(false),
    uploading: ref(false),
    busy: ref(false)
  }
  const accepted: unknown[] = []
  const scope = effectScope()
  const importer = scope.run(() =>
    useManagedImport({
      ...context,
      onAccepted: (value) => {
        accepted.push(value)
        return Promise.resolve()
      }
    })
  )!
  t.after(() => scope.stop())
  const storageKey = managedImportStorageKey('user-1', 'base-1')
  const requests: unknown[] = []
  const network: { admit: (init?: RequestInit) => Promise<Response> } = {
    admit: () => Promise.resolve(response(job, 202))
  }
  fetcher = async (input, init) => {
    const path = input instanceof Request ? input.url : input instanceof URL ? input.href : input
    if (path.startsWith('/api/files?'))
      return response({ items: [], total: 0, page: 1, page_size: 20 })
    if (path === '/api/knowledge/bases/base-1/documents') {
      if (typeof init?.body !== 'string') throw new Error('Expected a JSON request body')
      requests.push(JSON.parse(init.body))
      return network.admit(init)
    }
    throw new Error('Unexpected fixture path: ' + path)
  }
  const open = async () => {
    importer.openManagedImport()
    await flush()
    importer.toggleImport('original-file', true)
    importer.managedImport.size = 777
    importer.managedImport.overlap = 111
  }
  return { ...context, ...importer, stored, storageKey, network, open, requests, accepted, scope }
}

for (const change of ['base', 'user', 'selection', 'permission', 'selected-id', 'unmount']) {
  void test(`${change} invalidation aborts import and retains recovery despite a late success`, async (t) => {
    const f = fixture(t)
    let release!: (value: Response) => void
    let signal: AbortSignal | undefined
    f.network.admit = (init) =>
      new Promise<Response>((resolve) => {
        release = resolve
        signal = init?.signal ?? undefined
      })
    await f.open()
    const submitting = f.submitManagedImport()
    await flush()
    const saved = f.stored.get(f.storageKey)
    assert.ok(saved)
    if (change === 'base') f.baseId.value = 'base-2'
    if (change === 'user') f.userId.value = 'user-2'
    if (change === 'selection') f.selected.value = null
    if (change === 'permission') f.selected.value!.can_manage = false
    if (change === 'selected-id') f.selected.value = base('base-2')
    if (change === 'unmount') f.scope.stop()
    assert.equal(signal?.aborted, true)
    assert.equal(f.managedImport.open, false)
    assert.equal(f.managedImport.saving, false)
    assert.deepEqual(f.managedImport.names, [])
    assert.deepEqual(f.managedImport.ids, [])
    assert.deepEqual(f.managedImport.files, [])
    assert.equal(f.managedImport.key, '')
    release(response(job, 202))
    await submitting
    assert.deepEqual(f.accepted, [])
    assert.equal(f.stored.get(f.storageKey), saved)
    assert.equal(f.requests.length, 1)
  })
}

for (const status of [401, 403, 404]) {
  void test(`${status} on confirmation closes protected display and preserves the original draft`, async (t) => {
    const f = fixture(t)
    f.network.admit = () =>
      Promise.resolve(response({ error: { code: 'unavailable', message: 'Uncertain' } }, 503))
    await f.open()
    await f.submitManagedImport()
    const saved = f.stored.get(f.storageKey)
    assert.ok(saved)
    f.network.admit = () =>
      Promise.resolve(response({ error: { code: 'forbidden', message: 'Denied' } }, status))
    await f.submitManagedImport()
    assert.equal(f.managedImport.open, false)
    assert.deepEqual(f.managedImport.names, [])
    assert.equal(f.stored.get(f.storageKey), saved)
    assert.deepEqual(f.requests[1], f.requests[0])
  })
}

void test('restoring permission waits for explicit confirmation with every original request field', async (t) => {
  const f = fixture(t)
  f.network.admit = () =>
    Promise.resolve(response({ error: { code: 'unavailable', message: 'Uncertain' } }, 503))
  await f.open()
  await f.submitManagedImport()
  f.selected.value = null
  await flush()
  f.selected.value = base()
  f.folderId.value = 'different-folder'
  await flush()
  assert.equal(f.requests.length, 1)
  assert.equal(f.managedImport.open, false)
  f.openManagedImport()
  assert.equal(f.managedImport.uncertain, true)
  assert.equal(f.managedImport.folder, 'folder-original')
  assert.equal(f.managedImport.size, 777)
  assert.equal(f.managedImport.overlap, 111)
  f.network.admit = () => Promise.resolve(response(job, 202))
  await f.submitManagedImport()
  assert.deepEqual(f.requests, [f.requests[0], f.requests[0]])
  assert.equal(f.stored.has(f.storageKey), false)
  assert.equal(f.accepted.length, 1)
})
