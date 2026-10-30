import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { effectScope, nextTick, ref } from 'vue'
import type {
  GraphConfigState,
  GraphData,
  GraphJob
} from '../../src/modules/knowledge/api/graph.ts'
import type { KnowledgePreparation } from '../../src/modules/knowledge/api/index.ts'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const { useKnowledgeGraph } = await import(
  '../../src/modules/knowledge/composables/useKnowledgeGraph.ts'
)
const { useGraphQuery } = await import('../../src/modules/knowledge/composables/useGraphQuery.ts')
const { observePreparation } = await import('../../src/modules/knowledge/api/index.ts')

const stamp = '2026-10-30T00:00:00Z'
const job = (
  status: GraphJob['status'] = 'completed',
  overrides: Partial<GraphJob> = {}
): GraphJob => ({
  id: 'preview-1',
  base_id: 'base-1',
  operation: 'preview',
  status,
  phase: status,
  version: 2,
  attempt_id: 'attempt-1',
  graph_revision: 'revision-1',
  processed_documents: 1,
  total_documents: 1,
  progress: { extracted_chunks: 3, total_chunks: 3, entities: 2, relations: 1 },
  error: null,
  result: status === 'completed' ? { entities: 2, relations: 1, chunks: 3, seconds: 1 } : null,
  cancel_allowed: status !== 'completed' && status !== 'cancelled',
  created_at: stamp,
  updated_at: stamp,
  finished_at: status === 'completed' ? stamp : null,
  ...overrides
})
const root = '/api/knowledge/bases/base-1/graph'
const flush = async () => {
  await setImmediate()
  await setImmediate()
}
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })
class Visibility {
  hidden = true
  listeners = new Set<() => void>()
  addEventListener(_type: string, listener: () => void) {
    this.listeners.add(listener)
  }
  removeEventListener(_type: string, listener: () => void) {
    this.listeners.delete(listener)
  }
  show() {
    this.hidden = false
    for (const listener of this.listeners) listener()
  }
}
function hiddenDocument(t: TestContext) {
  const visibility = new Visibility(),
    previous = Object.getOwnPropertyDescriptor(globalThis, 'document')
  Object.defineProperty(globalThis, 'document', { configurable: true, value: visibility })
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'document', previous)
    else Reflect.deleteProperty(globalThis, 'document')
  })
  return visibility
}

class Fixture {
  counts = new Map<string, number>()
  rows: GraphJob[] = []
  current = job('running')
  config: GraphConfigState = {
    config: {
      version: 1,
      llm_model_id: 'model-1',
      reasoning_strategy: 'auto',
      timeout_seconds: 240,
      max_output_tokens: 16384,
      concurrency: 2
    },
    available: true,
    active_revision: 'revision-1',
    requires_rebuild: false,
    can_manage: true,
    supported_reasoning_strategies: ['auto', 'default']
  }
  graph: GraphData = {
    revision: 'revision-1',
    nodes: [
      {
        id: 'entity-1',
        entity_name: 'Entity',
        entity_type: 'organization',
        description: 'Description'
      }
    ],
    edges: [],
    is_truncated: false
  }
  handle?: (
    path: string,
    init?: RequestInit
  ) => Response | undefined | Promise<Response | undefined>
  constructor(t: TestContext) {
    const values = new Map<string, string>()
    const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
    Object.defineProperty(globalThis, 'sessionStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key)
      }
    })
    t.after(() => {
      if (previousStorage) Object.defineProperty(globalThis, 'sessionStorage', previousStorage)
      else Reflect.deleteProperty(globalThis, 'sessionStorage')
    })
    fetcher = async (input, init) => {
      const path = new URL(
        input instanceof Request ? input.url : input,
        'http://fixture'
      ).pathname.replace(root, '')
      this.counts.set(path, (this.counts.get(path) ?? 0) + 1)
      const override = await this.handle?.(path, init)
      if (override) return override
      if (path === '/config') return response(this.config)
      if (path === '/jobs') return response(this.rows)
      if (path === '/labels') return response({ items: ['entity-1'], truncated: false })
      if (path === '/subgraph') return response(this.graph)
      if (path === '/query') return response(this.current, 202)
      if (path.endsWith('/cancel'))
        return response(job('cancelled', { operation: this.current.operation, version: 4 }))
      if (path.startsWith('/jobs/')) return response(this.current)
      throw new Error('Unexpected fixture path: ' + path)
    }
  }
  count(path: string) {
    return this.counts.get(path) ?? 0
  }
  async mount(t: TestContext) {
    const enabled = ref(true),
      scope = effectScope()
    const warnings = t.mock.method(console, 'warn', () => undefined)
    const workspace = scope.run(() => useKnowledgeGraph(ref('base-1'), enabled))!
    warnings.mock.restore()
    t.after(async () => {
      enabled.value = false
      await nextTick()
      scope.stop()
    })
    await flush()
    assert.ok(workspace.state.value)
    return workspace
  }
}

void test('initial graph reads each resource once without observing settled history', async (t) => {
  const fixture = new Fixture(t)
  fixture.rows = [job()]
  await fixture.mount(t)
  assert.deepEqual(Object.fromEntries(fixture.counts), {
    '/config': 1,
    '/jobs': 1,
    '/subgraph': 1,
    '/labels': 1
  })
})

void test('an unchanged manual refresh keeps graph and config identities and skips the page loading state', async (t) => {
  const fixture = new Fixture(t),
    workspace = await fixture.mount(t)
  const graph = workspace.graph.value,
    config = workspace.state.value!.config
  const refreshing = workspace.load()
  assert.equal(workspace.loading.value, false)
  await refreshing
  await flush()
  assert.equal(workspace.graph.value, graph)
  assert.equal(workspace.state.value!.config, config)
})

void test('canonical history removes omitted previews but retains newer versions of listed jobs', async (t) => {
  const fixture = new Fixture(t)
  fixture.rows = [job()]
  const workspace = await fixture.mount(t)
  fixture.rows = [job('running', { version: 1 })]
  await workspace.load()
  assert.equal(workspace.preview.value!.status, 'completed')
  assert.equal(workspace.preview.value!.version, 2)
  fixture.rows = []
  await workspace.load()
  assert.deepEqual(workspace.jobs.value, [])
  assert.equal(workspace.preview.value, null)
})

void test('progress reads only the active job and synchronizes canonical resources once at completion', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  const workspace = await fixture.mount(t),
    graph = workspace.graph.value
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 1)
  assert.equal(fixture.count('/config'), 1)
  assert.equal(fixture.count('/subgraph'), 1)
  assert.equal(workspace.graph.value, graph)
  fixture.current = job('completed', { version: 3 })
  fixture.rows = [fixture.current]
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 2)
  for (const path of ['/config', '/jobs', '/subgraph', '/labels'])
    assert.equal(fixture.count(path), 2, path)
  assert.equal(workspace.loading.value, false)
  t.mock.timers.tick(30_000)
  await flush()
  assert.equal(fixture.count('/config'), 2)
})

void test('refresh invalidates a delayed observer before accepting a filtered canonical history', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  const workspace = await fixture.mount(t)
  let release!: (value: Response) => void
  fixture.handle = (path) =>
    path === '/jobs/preview-1'
      ? new Promise((resolve) => {
          release = resolve
        })
      : undefined
  t.mock.timers.tick(1500)
  await flush()
  assert.ok(release)
  fixture.rows = []
  await workspace.load()
  release(response(job('completed', { version: 9 })))
  await flush()
  assert.deepEqual(workspace.jobs.value, [])
  t.mock.timers.tick(15_000)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 1)
})

void test('transient observer errors back off while retaining the canvas and clear after recovery', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  let failures = 2
  fixture.handle = (path) =>
    path === '/jobs/preview-1' && failures-- > 0
      ? response({ error: { code: 'temporary', message: 'Observation unavailable' } }, 503)
      : undefined
  const workspace = await fixture.mount(t),
    graph = workspace.graph.value
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(workspace.graph.value, graph)
  assert.equal(workspace.error.value, 'Observation unavailable')
  t.mock.timers.tick(2999)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 1)
  t.mock.timers.tick(1)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 2)
  t.mock.timers.tick(6000)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 3)
  assert.equal(workspace.error.value, '')
  assert.equal(workspace.graph.value, graph)
})

void test('a manual graph selection during a refresh preserves background job observation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  const workspace = await fixture.mount(t)
  let release!: (value: Response) => void
  fixture.handle = (path) =>
    path === '/subgraph' && fixture.count(path) === 2
      ? new Promise((resolve) => {
          release = resolve
        })
      : undefined
  const refreshing = workspace.load()
  await flush()
  assert.ok(release)
  await workspace.loadGraph({ node_label: 'entity-1', max_depth: 1, max_nodes: 20 })
  release(response(fixture.graph))
  await refreshing
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 1)
})

void test('graph query observations use the background interval and explicit cancellation remains available', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.current = job('running', { operation: 'query' })
  const warnings = t.mock.method(console, 'warn', () => undefined)
  const query = useGraphQuery(ref('base-1'))
  warnings.mock.restore()
  t.after(() => query.reset())
  const submitted = query.submit('Find entity', 'mix')
  await flush()
  t.mock.timers.tick(1499)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 0)
  await query.stop()
  await submitted
  assert.equal(fixture.count('/jobs/preview-1/cancel'), 1)
  assert.equal(query.busy.value, false)
  assert.equal(query.recoverable.value, false)
})

void test('hidden graph observations pause and returning to the tab observes once immediately', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const visibility = hiddenDocument(t),
    fixture = new Fixture(t)
  fixture.rows = [job('running')]
  await fixture.mount(t)
  t.mock.timers.tick(30_000)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 0)
  visibility.show()
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 1)
})

void test('knowledge preparation observation pauses in hidden tabs and resumes immediately', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const visibility = hiddenDocument(t),
    fixture = new Fixture(t)
  const initial: KnowledgePreparation = {
    kind: 'knowledge_preparation',
    id: 'preparation-1',
    resource_type: 'chat',
    resource_id: 'conversation-1',
    status: 'searching',
    phase: 'searching',
    run: null,
    hits: [],
    error: null,
    created_at: stamp,
    updated_at: stamp
  }
  const path = '/api/knowledge/preparations/preparation-1'
  fixture.handle = (requested) =>
    requested === path ? response({ ...initial, status: 'completed' }) : undefined
  const controller = new AbortController()
  t.after(() => controller.abort())
  const observing = observePreparation(initial, () => undefined, controller.signal)
  t.mock.timers.tick(30_000)
  await flush()
  assert.equal(fixture.count(path), 0)
  visibility.show()
  assert.equal((await observing).status, 'completed')
  assert.equal(fixture.count(path), 1)
  assert.equal(visibility.listeners.size, 0)
})

void test('a write completing after protected results were invalidated reauthorizes through canonical history', async (t) => {
  const fixture = new Fixture(t),
    workspace = await fixture.mount(t)
  let release!: (value: Response) => void
  fixture.handle = (path) =>
    path === '/preview'
      ? new Promise((resolve) => {
          release = resolve
        })
      : undefined
  const starting = workspace.start({ ...fixture.config.config! }, 'preview')
  await flush()
  assert.ok(release)
  fixture.handle = (path) =>
    path === '/subgraph'
      ? response({ error: { code: 'source_denied', message: 'Source permission revoked' } }, 403)
      : undefined
  await workspace.loadGraph()
  assert.equal(workspace.graph.value, null)
  release(response(job(), 202))
  await starting
  await flush()
  assert.deepEqual(workspace.jobs.value, [])
  assert.equal(fixture.count('/jobs'), 2)
})

void test('an authoritative source change clears the previously rendered graph and preview', async (t) => {
  const fixture = new Fixture(t)
  fixture.rows = [job()]
  const workspace = await fixture.mount(t)
  fixture.handle = (path) =>
    path === '/subgraph'
      ? response(
          { error: { code: 'graph_source_changed', message: 'Source content changed' } },
          409
        )
      : undefined
  await workspace.loadGraph()
  assert.equal(workspace.graph.value, null)
  assert.equal(workspace.preview.value!.result, null)
})

void test('an aborted older query restore cannot clear the result of a newer query', async (t) => {
  const fixture = new Fixture(t)
  const warnings = t.mock.method(console, 'warn', () => undefined)
  const query = useGraphQuery(ref('base-1'))
  warnings.mock.restore()
  t.after(() => query.reset())
  sessionStorage.setItem(
    'zhiheng:graph-query::base-1',
    JSON.stringify({ intent: null, jobId: 'older-query', cancelRequested: false, settled: true })
  )
  let release!: (value: Response) => void
  fixture.handle = (path) =>
    path === '/jobs/older-query'
      ? new Promise((resolve) => {
          release = resolve
        })
      : undefined
  const restoring = query.restore()
  await flush()
  query.reset()
  fixture.current = job('completed', {
    operation: 'query',
    result: { context: 'Newer result', entities: [], relationships: [], sources: [] }
  })
  await query.submit('Newer query', 'mix')
  assert.equal(query.result.value?.context, 'Newer result')
  release(response(fixture.current))
  await restoring
  assert.equal(query.result.value?.context, 'Newer result')
  assert.equal(query.error.value, '')
  assert.equal(query.recoverable.value, false)
})

void test('observer failures and recovery preserve an uncertain cancellation error', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  const workspace = await fixture.mount(t)
  let failObservation = true
  fixture.handle = (path) => {
    if (path.endsWith('/cancel'))
      return response(
        { error: { code: 'cancel_unknown', message: 'Cancellation acceptance is unknown' } },
        503
      )
    if (path === '/jobs/preview-1' && failObservation) {
      failObservation = false
      return response(
        { error: { code: 'read_unavailable', message: 'Observation unavailable' } },
        503
      )
    }
  }
  await workspace.cancel(workspace.preview.value!)
  assert.equal(workspace.error.value, 'Cancellation acceptance is unknown')
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(workspace.error.value, 'Cancellation acceptance is unknown')
  t.mock.timers.tick(3000)
  await flush()
  assert.equal(workspace.error.value, 'Cancellation acceptance is unknown')
  assert.equal(workspace.recoverable.value, true)
  assert.equal(fixture.count('/jobs/preview-1/cancel'), 1)
  const saved = JSON.parse(sessionStorage.getItem('zhiheng:graph-intent::base-1')!) as Record<
    string,
    unknown
  >
  assert.equal(saved.cancelId, 'preview-1')
})

void test('a failed manual refresh resumes observation of a known active job', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  const workspace = await fixture.mount(t),
    graph = workspace.graph.value
  fixture.handle = (path) =>
    path === '/config'
      ? response({ error: { code: 'refresh_unavailable', message: 'Refresh unavailable' } }, 503)
      : undefined
  await workspace.load()
  assert.equal(workspace.graph.value, graph)
  t.mock.timers.tick(1500)
  await flush()
  assert.equal(fixture.count('/jobs/preview-1'), 1)
})

void test('identical gateway messages cannot make observation recovery clear a cancellation error', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const fixture = new Fixture(t)
  fixture.rows = [job('running')]
  const workspace = await fixture.mount(t)
  let failObservation = true
  fixture.handle = (path) => {
    if (path.endsWith('/cancel') || (path === '/jobs/preview-1' && failObservation)) {
      if (!path.endsWith('/cancel')) failObservation = false
      return response({ error: { code: 'gateway_error', message: 'Request failed (503)' } }, 503)
    }
  }
  await workspace.cancel(workspace.preview.value!)
  t.mock.timers.tick(1500)
  await flush()
  t.mock.timers.tick(3000)
  await flush()
  assert.equal(workspace.error.value, 'Request failed (503)')
  assert.equal(workspace.recoverable.value, true)
  assert.equal(fixture.count('/jobs/preview-1/cancel'), 1)
})
