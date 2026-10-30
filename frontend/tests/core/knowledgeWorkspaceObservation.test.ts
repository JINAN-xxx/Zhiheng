import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, effectScope, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { KnowledgeBase, KnowledgeJob } from '../../src/modules/knowledge/api/index.ts'
import type { GraphJob } from '../../src/modules/knowledge/api/graph.ts'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const { useKnowledgeWorkspace } = await import(
  '../../src/modules/knowledge/composables/workspace.ts'
)
const { useKnowledgeQuery } = await import(
  '../../src/modules/knowledge/composables/useKnowledgeQuery.ts'
)
const stamp = '2026-10-30T00:00:00Z'
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
const base = (id = 'base-1'): KnowledgeBase => ({
  id,
  name: id,
  description: 'Description',
  owner_id: 'user-1',
  access: 'private',
  version: 1,
  acl_version: 1,
  embed_model_id: 'embed-1',
  chunk_size: 1000,
  chunk_overlap: 200,
  active_revision: 'revision-1',
  active_job_id: 'job-active',
  index_status: 'building',
  document_count: 1,
  last_error: null,
  enable_knowledge_graph: false,
  llm_model_id: null,
  graph_status: 'disabled',
  created_at: stamp,
  updated_at: stamp,
  can_manage: true
})
const job = (): KnowledgeJob => ({
  id: 'job-active',
  base_id: 'base-1',
  operation: 'rechunk',
  status: 'waiting_model',
  phase: 'embedding',
  version: 1,
  attempt_id: 'attempt-1',
  index_revision: null,
  total_documents: 1,
  completed_documents: 0,
  failed_documents: 0,
  chunk_count: null,
  document_results: [],
  error: null,
  cancel_allowed: true,
  created_at: stamp,
  updated_at: stamp,
  finished_at: null
})
class Fixture {
  base = base()
  otherBase = { ...base('base-2'), active_job_id: null, index_status: 'ready' as const }
  job = job()
  counts = new Map<string, number>()
  overviewErrors: number[] = []
  override?: (
    path: string,
    init?: RequestInit
  ) => Promise<Response | undefined> | Response | undefined
  constructor(t: TestContext) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
    const values = new Map<string, string>()
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key)
      }
    })
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, 'sessionStorage', previous)
      else Reflect.deleteProperty(globalThis, 'sessionStorage')
    })
    fetcher = async (input, init) => {
      const path = new URL(input instanceof Request ? input.url : input, 'http://fixture').pathname
      this.counts.set(path, this.count(path) + 1)
      const custom = await this.override?.(path, init)
      if (custom) return custom
      if (path === '/api/knowledge/capabilities')
        return response({
          milvus_configured: true,
          milvus_available: true,
          native_formats: ['txt'],
          graph_available: false,
          mineru_available: false
        })
      if (path === '/api/models') return response([])
      if (path === '/api/knowledge/bases') {
        const status = this.count(path) > 1 ? this.overviewErrors.shift() : undefined
        return status
          ? response(
              { error: { code: 'overview_unavailable', message: 'Overview unavailable' } },
              status
            )
          : response([this.base, this.otherBase])
      }
      if (path === '/api/knowledge/bases/base-1') return response(this.base)
      if (path === '/api/knowledge/bases/base-2') return response(this.otherBase)
      if (path.endsWith('/documents')) return response({ items: [], total: 0 })
      if (path.endsWith('/folders')) return response([])
      if (path.endsWith('/jobs')) return response(path.includes('base-1') ? [this.job] : [])
      if (path === '/api/knowledge/jobs/job-active') return response(this.job)
      throw new Error('Unexpected fixture path: ' + path)
    }
  }
  count(path: string): number {
    return this.counts.get(path) ?? 0
  }
  complete(): void {
    Object.assign(this.job, {
      status: 'completed',
      version: 2,
      cancel_allowed: false,
      completed_documents: 1,
      finished_at: stamp
    })
    Object.assign(this.base, { active_job_id: null, index_status: 'ready' })
  }
  async mount(t: TestContext) {
    let workspace!: ReturnType<typeof useKnowledgeWorkspace>
    const component = {
      setup() {
        workspace = useKnowledgeWorkspace()
        return () => null
      }
    }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/knowledge/:baseId/:view', component }]
    })
    await router.push('/knowledge/base-1/documents')
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
    const warnings = t.mock.method(console, 'warn', () => undefined)
    const app = renderer.createApp(component)
    app.use(router)
    app.mount({})
    warnings.mock.restore()
    t.after(() => app.unmount())
    await flush()
    assert.equal(workspace.selected.value?.active_job_id, 'job-active')
    return { workspace, router }
  }
}

void test('terminal overview failures retry only the overview after documents have synced', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.overviewErrors = [503, 503]
  const { workspace } = await fixture.mount(t)
  fixture.complete()
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(workspace.selected.value?.index_status, 'ready')
  assert.equal(workspace.bases.value[0]?.index_status, 'building')
  assert.equal(fixture.count('/api/knowledge/bases/base-1/documents'), 2)
  t.mock.timers.tick(2999)
  await flush()
  assert.equal(fixture.count('/api/knowledge/bases'), 2)
  t.mock.timers.tick(1)
  await flush()
  assert.equal(fixture.count('/api/knowledge/bases'), 3)
  t.mock.timers.tick(6000)
  await flush()
  assert.equal(fixture.count('/api/knowledge/bases'), 4)
  assert.equal(workspace.bases.value[0]?.index_status, 'ready')
  assert.equal(workspace.listError.value, '')
  assert.equal(workspace.observationError.value, '')
  assert.equal(fixture.count('/api/knowledge/bases/base-1/documents'), 2)
  assert.equal(fixture.count('/api/knowledge/bases/base-1/folders'), 2)
  assert.equal(fixture.count('/api/knowledge/jobs/job-active'), 1)
  t.mock.timers.tick(30_000)
  await flush()
  assert.equal(fixture.count('/api/knowledge/bases'), 4)
})

void test('terminal overview permission failure clears protected data and ends observation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.overviewErrors = [403]
  const { workspace } = await fixture.mount(t)
  fixture.complete()
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(workspace.selected.value, null)
  assert.deepEqual(workspace.documents.value, [])
  assert.deepEqual(workspace.jobs.value, [])
  t.mock.timers.tick(30_000)
  await flush()
  assert.equal(fixture.count('/api/knowledge/bases'), 2)
})

void test('changing base aborts a pending terminal overview read and rejects its late response', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  const { workspace, router } = await fixture.mount(t)
  let release!: (value: Response) => void
  let signal: AbortSignal | undefined
  fixture.override = (path, init) =>
    path === '/api/knowledge/bases'
      ? new Promise<Response>((resolve) => {
          release = resolve
          signal = init?.signal ?? undefined
        })
      : undefined
  fixture.complete()
  t.mock.timers.tick(1500)
  await flush()
  await router.push('/knowledge/base-2/documents')
  await flush()
  assert.equal(signal?.aborted, true)
  release(response([fixture.base, fixture.otherBase]))
  await flush()
  assert.equal(workspace.selected.value?.id, 'base-2')
  assert.equal(workspace.bases.value[0]?.index_status, 'building')
})

const graphJob = (baseId: string): GraphJob => ({
  id: 'new-query',
  base_id: baseId,
  operation: 'query',
  status: 'completed',
  phase: 'completed',
  version: 1,
  attempt_id: 'attempt-1',
  graph_revision: 'revision-1',
  processed_documents: 1,
  total_documents: 1,
  progress: { extracted_chunks: 1, total_chunks: 1, entities: 0, relations: 0 },
  error: null,
  result: { context: 'New graph result', entities: [], relationships: [], sources: [] },
  cancel_allowed: false,
  created_at: stamp,
  updated_at: stamp,
  finished_at: stamp
})
for (const destination of ['base-1', 'base-2']) {
  void test(`old selected-query restore cannot hide a new graph result for ${destination}`, async (t) => {
    const fixture = new Fixture(t)
    sessionStorage.setItem(
      'zhiheng:graph-query::base-1',
      JSON.stringify({ intent: null, jobId: 'old-query', cancelRequested: false, settled: true })
    )
    sessionStorage.setItem(
      'zhiheng:knowledge-query::base-1:request',
      JSON.stringify({ query: 'Old vector intent', client_request_id: 'old-intent' })
    )
    let release!: (value: Response) => void
    fixture.override = (path) =>
      path === '/api/knowledge/bases/base-1/graph/jobs/old-query'
        ? new Promise<Response>((resolve) => {
            release = resolve
          })
        : path === `/api/knowledge/bases/${destination}/graph/query`
          ? response(graphJob(destination), 202)
          : undefined
    const id = ref('base-1'),
      scope = effectScope()
    const warnings = t.mock.method(console, 'warn', () => undefined)
    const query = scope.run(() => useKnowledgeQuery(id, ref(null)))!
    warnings.mock.restore()
    t.after(() => {
      query.resetQuery()
      scope.stop()
    })
    const restoring = query.restoreSelectedQuery('base-1')
    await flush()
    id.value = destination
    query.resetQuery()
    await query.submitSelectedQuery('New graph intent', 'graph')
    assert.equal(query.graphTarget.value, true)
    assert.equal(query.graphQuery.result.value?.context, 'New graph result')
    release(response(graphJob(destination)))
    await restoring
    assert.equal(query.graphTarget.value, true)
    assert.equal(query.graphQuery.result.value?.context, 'New graph result')
    assert.equal(query.queryRecoverable.value, false)
    await query.restoreSelectedQuery('stale-base')
    assert.equal(query.graphTarget.value, true)
    assert.equal(fixture.count(`/api/knowledge/bases/${destination}/graph/jobs/new-query`), 0)
  })
}
