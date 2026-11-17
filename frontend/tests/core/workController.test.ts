import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { createRenderer, defineComponent, h } from 'vue'
import type { WorkTask } from '../../src/modules/work/api/index.ts'
import type { RunSnapshot } from '../../src/shared/stream.ts'
import type { KnowledgePreparation } from '../../src/modules/knowledge/public.ts'
import { grantedPermissions } from './permissionFixture.ts'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
after(() => {
  globalThis.fetch = originalFetch
})
const { useWorkTasks } = await import('../../src/modules/work/composables/tasks.ts')
const { useWorkRunObservation } = await import(
  '../../src/modules/work/composables/runObservation.ts'
)
const { useWorkTurnCommands } = await import('../../src/modules/work/composables/turns.ts')
const { provideWorkWorkspace, useWorkWorkspace } = await import('../../src/modules/work/store.ts')
const { clearIdentity, restoreIdentity } = await import('../../src/modules/identity/public.ts')
type Commands = ReturnType<typeof useWorkTurnCommands>
type Observation = ReturnType<typeof useWorkRunObservation>

const stamp = '2026-11-15T00:00:00Z'
const response = (value: unknown) =>
  new Response(JSON.stringify(value), {
    headers: { 'Content-Type': 'application/json' }
  })
function task(id = 'task-1', version = 1): WorkTask {
  return {
    taskId: id,
    ownerId: 'user-1',
    title: id,
    goal: 'goal',
    status: 'active',
    version,
    currentRunId: null,
    preparationId: null,
    activeAgentId: 'agent-a',
    createClientRequestId: 'create-1',
    createdAt: stamp,
    updatedAt: stamp,
    members: [],
    messages: []
  }
}
function rawTask(value: WorkTask) {
  return {
    id: value.taskId,
    owner_id: value.ownerId,
    title: value.title,
    goal: value.goal,
    status: value.status,
    version: value.version,
    current_run_id: value.currentRunId,
    preparation_id: value.preparationId,
    active_agent_id: value.activeAgentId,
    create_client_request_id: value.createClientRequestId,
    created_at: value.createdAt,
    updated_at: value.updatedAt,
    members: [],
    messages: []
  }
}
function run(status: RunSnapshot['status'] = 'running', revision = 1): RunSnapshot {
  return {
    id: 'run-1',
    resource_type: 'work_turn',
    resource_id: 'task-1',
    status,
    attempt_id: 'attempt-1',
    attempt_no: 1,
    revision,
    projection_seq: revision,
    text: 'result-' + revision,
    reasoning: '',
    error: null,
    created_at: stamp,
    usage: { input_tokens: null, output_tokens: null, status: 'pending' }
  }
}
function preparation(
  status: KnowledgePreparation['status'] = 'waiting_models'
): KnowledgePreparation {
  return {
    kind: 'knowledge_preparation',
    id: 'prep-1',
    resource_type: 'work_turn',
    resource_id: 'task-1',
    status,
    phase: 'embedding',
    run: null,
    hits: [],
    error: null,
    created_at: stamp,
    updated_at: stamp
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}
async function flush() {
  for (let index = 0; index < 30; index++) await Promise.resolve()
}
function controllers(t: TestContext) {
  const resources = useWorkTasks({
    canRead: () => true,
    recordTask: (value) => commands.recordTask(value),
    projectCurrentRun: () => observation.projectCurrentRun()
  })
  const observation: Observation = useWorkRunObservation({
    tasks: resources,
    canObserve: () => true,
    runSettled: (value) => commands.runSettled(value),
    preparationSettled: (value, id) => commands.preparationSettled(value, id)
  })
  const commands: Commands = useWorkTurnCommands({
    tasks: resources,
    observation,
    canCreate: () => true,
    canEdit: () => true,
    canRun: () => true,
    ownerId: () => 'user-1',
    reloadTask: async (id) => {
      await resources.loadTask(id)
    }
  })
  function select(value: WorkTask) {
    resources.stopLocalActivity()
    observation.reset()
    commands.stopLocalActivity()
    resources.selectedTaskId.value = value.taskId
    resources.currentTask.value = value
  }
  t.after(() => {
    resources.dispose()
    commands.dispose()
    observation.dispose()
  })
  return { resources, observation, commands, select }
}

function mountedWorkspace(t: TestContext) {
  let workspace!: ReturnType<typeof useWorkWorkspace>
  const child = defineComponent({
    setup() {
      workspace = useWorkWorkspace()
      return () => null
    }
  })
  const root = defineComponent({
    setup() {
      provideWorkWorkspace()
      return () => h(child)
    }
  })
  const renderer = createRenderer<object, object>({
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
  app.mount({})
  t.after(() => {
    app.unmount()
    clearIdentity()
  })
  return workspace
}

void test('switching tasks rejects a delayed detail and its old ownership ticket', async (t) => {
  const { resources, select } = controllers(t)
  const held = deferred<Response>()
  let signal: AbortSignal | null | undefined
  fetcher = (_input, init) => {
    signal = init?.signal
    return held.promise
  }
  select(task())
  const old = resources.refreshTask('task-1', resources.taskScope.current())
  select(task('task-2'))
  assert.equal(signal?.aborted, true)
  held.resolve(response(rawTask({ ...task(), title: 'old private detail' })))
  await assert.rejects(old, { name: 'AbortError' })
  assert.equal(resources.currentTask.value?.taskId, 'task-2')
})

void test('overlapping detail reads cannot replace a newer task version', async (t) => {
  const { resources, select } = controllers(t)
  const held = deferred<Response>()
  fetcher = () => held.promise
  select(task())
  const owner = resources.taskScope.current()
  const old = resources.refreshTask('task-1', owner)
  resources.acceptTask({ ...task('task-1', 3), title: 'new title' }, owner)
  held.resolve(response(rawTask({ ...task('task-1', 2), title: 'stale title' })))
  await old
  assert.equal(resources.currentTask.value?.version, 3)
  assert.equal(resources.currentTask.value?.title, 'new title')
})

void test('concurrent stop requests share one operation and a late reply cannot reopen a terminal run', async (t) => {
  const { resources, observation, select } = controllers(t)
  select({ ...task(), currentRunId: 'run-1' })
  observation.adoptRun(run(), 'task-1')
  const held = deferred<Response>()
  let cancels = 0,
    events = 0
  fetcher = (input) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path.endsWith('/cancel')) {
      cancels++
      return held.promise
    }
    if (path.endsWith('/events')) {
      events++
      throw new Error('terminal runs must not reopen streams')
    }
    return Promise.resolve(response(rawTask(task('task-1', 2))))
  }
  const first = observation.cancelRun(),
    second = observation.cancelRun()
  assert.equal(first, second)
  assert.equal(cancels, 1)
  observation.adoptRun(run('completed', 5), 'task-1')
  held.resolve(response(run('running', 2)))
  await first
  assert.equal(observation.activeRun.value?.status, 'completed')
  assert.equal(observation.activeRun.value?.revision, 5)
  assert.equal(events, 0)
  assert.equal(resources.currentTask.value?.currentRunId, null)
})

void test('stopping a preparation follows admission into its run and cancels that run once', async (t) => {
  const { observation, select } = controllers(t)
  select({ ...task(), preparationId: 'prep-1' })
  observation.activePreparation.value = preparation()
  observation.activeRun.value = { ...run('completed', 5), id: 'previous-run' }
  const calls: string[] = []
  fetcher = (input) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    calls.push(path)
    if (path === '/api/knowledge/preparations/prep-1/cancel')
      return Promise.resolve(response({ ...preparation('admitted'), run: run() }))
    if (path === '/api/work/tasks/task-1/runs/run-1/cancel')
      return Promise.resolve(response(run('cancel_requested', 2)))
    throw new Error('Unexpected request ' + path)
  }
  const first = observation.cancelRun(false),
    second = observation.cancelRun(false)
  assert.equal(first, second)
  await first
  assert.deepEqual(calls, [
    '/api/knowledge/preparations/prep-1/cancel',
    '/api/work/tasks/task-1/runs/run-1/cancel'
  ])
  assert.equal(observation.activeRun.value?.id, 'run-1')
  assert.equal(observation.activeRun.value?.status, 'cancel_requested')
  assert.equal(observation.pendingStops.has('task-1'), false)
})

void test('a batch of member commands keeps the task selected when the batch started', async (t) => {
  const { resources, commands, select } = controllers(t)
  select(task())
  const held = deferred<Response>()
  const writes: string[] = []
  fetcher = (input, init) => {
    if (init?.method === 'POST') {
      writes.push(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url)
      return held.promise
    }
    return Promise.resolve(response({ items: [], total: 0 }))
  }
  const adding = commands.addMembers(['agent-b', 'agent-c'])
  select(task('task-2'))
  held.resolve(response(rawTask(task('task-1', 2))))
  await adding
  assert.deepEqual(writes, ['/api/work/tasks/task-1/members'])
  assert.equal(resources.currentTask.value?.taskId, 'task-2')
})

void test('revoking Work commands invalidates pending writes while retaining authorized reads', async (t) => {
  clearIdentity()
  let permissions = grantedPermissions('platform:work:use', 'platform:work:update')
  const held = deferred<Response>()
  let mutationSignal: AbortSignal | null | undefined
  let writes = 0
  fetcher = (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path === '/api/auth/me')
      return Promise.resolve(
        response({
          id: 'user-1',
          username: 'owner',
          is_admin: false,
          permissions
        })
      )
    if (path === '/api/work/tasks/task-1' && init?.method === 'PATCH') {
      writes++
      mutationSignal = init.signal
      return held.promise
    }
    if (path === '/api/work/tasks/task-1') return Promise.resolve(response(rawTask(task())))
    if (path.startsWith('/api/work/agents/available')) return Promise.resolve(response([]))
    if (path.startsWith('/api/work/tasks?'))
      return Promise.resolve(response({ items: [], total: 0 }))
    throw new Error('Unexpected request ' + path)
  }
  await restoreIdentity(true)
  const workspace = mountedWorkspace(t)
  await workspace.loadTask('task-1')
  const saving = workspace.saveTask({ title: 'unacknowledged title' })
  permissions = grantedPermissions('platform:work:read')
  await restoreIdentity(true)
  await flush()
  assert.equal(mutationSignal?.aborted, true)
  held.resolve(response(rawTask({ ...task('task-1', 2), title: 'late title' })))
  assert.equal(await saving, false)
  assert.equal(workspace.currentTask?.title, 'task-1')
  assert.equal(await workspace.saveTask({ title: 'unauthorized title' }), false)
  assert.equal(writes, 1)
})

void test('recovering a lost preparation stop cancels its admitted run without retrying the old preparation', async (t) => {
  const { observation, select } = controllers(t)
  const pending = { ...task(), preparationId: 'prep-1' }
  select(pending)
  observation.activePreparation.value = preparation()
  let admitted = false
  let stopped = false
  let preparationStops = 0
  let runStops = 0
  fetcher = (input) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path === '/api/knowledge/preparations/prep-1/cancel') {
      preparationStops++
      admitted = true
      return Promise.resolve(
        new Response(
          JSON.stringify({ error: { code: 'unavailable', message: 'Stop result unknown' } }),
          {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
          }
        )
      )
    }
    if (path === '/api/work/tasks/task-1')
      return Promise.resolve(
        response(
          rawTask(
            stopped
              ? task('task-1', 3)
              : admitted
                ? { ...task('task-1', 2), currentRunId: 'run-1' }
                : pending
          )
        )
      )
    if (path === '/api/work/tasks/task-1/runs/run-1') return Promise.resolve(response(run()))
    if (path === '/api/work/tasks/task-1/runs/run-1/cancel') {
      runStops++
      stopped = true
      return Promise.resolve(response(run('cancelled', 2)))
    }
    if (path.startsWith('/api/work/tasks?'))
      return Promise.resolve(response({ items: [], total: 0 }))
    throw new Error('Unexpected request ' + path)
  }
  await observation.cancelRun()
  assert.equal(observation.disconnected.value, true)
  assert.equal(observation.pendingStops.has('task-1'), true)
  await observation.reconnect()
  await flush()
  assert.equal(preparationStops, 1)
  assert.equal(runStops, 1)
  assert.equal(observation.activePreparation.value, null)
  assert.equal(observation.activeRun.value?.status, 'cancelled')
  assert.equal(observation.pendingStops.has('task-1'), false)
})

void test('a read-only Work owner can recover and stop an existing run without admitting another', async (t) => {
  clearIdentity()
  let readable = false,
    active = true,
    runReads = 0,
    cancels = 0,
    admissions = 0
  fetcher = (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path === '/api/auth/me')
      return Promise.resolve(
        response({
          id: 'user-1',
          username: 'owner',
          is_admin: false,
          permissions: grantedPermissions('platform:work:read')
        })
      )
    if (path === '/api/work/tasks/task-1/runs' && init?.method === 'POST') {
      admissions++
      throw new Error('Read-only owners must not admit a new run')
    }
    if (path === '/api/work/tasks/task-1/runs/run-1/cancel') {
      cancels++
      active = false
      return Promise.resolve(response(run('cancelled', 2)))
    }
    if (path === '/api/work/tasks/task-1/runs/run-1') {
      runReads++
      return Promise.resolve(
        readable
          ? response(run())
          : new Response(
              JSON.stringify({
                error: { code: 'unavailable', message: 'Observation unavailable' }
              }),
              { status: 503, headers: { 'Content-Type': 'application/json' } }
            )
      )
    }
    if (path.endsWith('/events'))
      return Promise.resolve(
        new Response(
          JSON.stringify({ error: { code: 'unavailable', message: 'Stream unavailable' } }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        )
      )
    if (path === '/api/work/tasks/task-1')
      return Promise.resolve(
        response(rawTask(active ? { ...task(), currentRunId: 'run-1' } : task('task-1', 2)))
      )
    if (path.startsWith('/api/work/agents/available')) return Promise.resolve(response([]))
    if (path.startsWith('/api/work/tasks?'))
      return Promise.resolve(response({ items: [], total: 0 }))
    throw new Error('Unexpected request ' + path)
  }
  await restoreIdentity(true)
  const workspace = mountedWorkspace(t)
  await workspace.loadTask('task-1')
  assert.equal(workspace.disconnected, true)
  assert.equal(runReads, 1)
  readable = true
  await workspace.retryRun()
  await flush()
  assert.equal(runReads, 2)
  assert.equal(workspace.canStop, true)
  await workspace.cancelRun()
  await flush()
  assert.equal(cancels, 1)
  assert.equal(workspace.canStop, false)
  assert.equal(workspace.currentTask?.taskId, 'task-1')
  assert.equal(workspace.submissionBlocked, false)
  assert.equal(
    await workspace.runMessage({ agentId: 'agent-a', message: 'Forbidden new turn' }),
    false
  )
  assert.equal(admissions, 0)
})

void test('a read-only owner recovers an unknown preparation stop into one run cancellation', async (t) => {
  clearIdentity()
  let admitted = false,
    stopped = false,
    preparationStops = 0,
    runStops = 0,
    admissions = 0
  fetcher = (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path === '/api/auth/me')
      return Promise.resolve(
        response({
          id: 'user-1',
          username: 'owner',
          is_admin: false,
          permissions: grantedPermissions('platform:work:read')
        })
      )
    if (path === '/api/knowledge/preparations/prep-1/cancel') {
      preparationStops++
      admitted = true
      return Promise.resolve(
        new Response(
          JSON.stringify({ error: { code: 'unavailable', message: 'Stop result unknown' } }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        )
      )
    }
    if (path === '/api/knowledge/preparations/prep-1')
      return Promise.resolve(
        new Response(
          JSON.stringify({
            error: { code: 'unavailable', message: 'Preparation observation unavailable' }
          }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        )
      )
    if (path === '/api/work/tasks/task-1/runs/run-1/cancel') {
      runStops++
      stopped = true
      return Promise.resolve(response(run('cancelled', 2)))
    }
    if (path === '/api/work/tasks/task-1/runs/run-1') return Promise.resolve(response(run()))
    if (path === '/api/work/tasks/task-1/runs' && init?.method === 'POST') {
      admissions++
      throw new Error('Stop recovery must not admit another run')
    }
    if (path === '/api/work/tasks/task-1')
      return Promise.resolve(
        response(
          rawTask(
            stopped
              ? task('task-1', 3)
              : admitted
                ? { ...task('task-1', 2), currentRunId: 'run-1' }
                : { ...task(), preparationId: 'prep-1' }
          )
        )
      )
    if (path.startsWith('/api/work/agents/available')) return Promise.resolve(response([]))
    if (path.startsWith('/api/work/tasks?'))
      return Promise.resolve(response({ items: [], total: 0 }))
    throw new Error('Unexpected request ' + path)
  }
  await restoreIdentity(true)
  const workspace = mountedWorkspace(t)
  await workspace.loadTask('task-1')
  await workspace.cancelRun()
  assert.equal(preparationStops, 1)
  assert.equal(workspace.disconnected, true)
  await workspace.retryRun()
  await flush()
  assert.equal(preparationStops, 1)
  assert.equal(runStops, 1)
  assert.equal(workspace.canStop, false)
  assert.equal(workspace.runLocked, false)
  assert.equal(admissions, 0)
})

void test('Work read and use rights never authorize observing or cancelling another owner task', async (t) => {
  clearIdentity()
  const runtimeRequests: string[] = []
  let busy = true
  fetcher = (input) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path === '/api/auth/me')
      return Promise.resolve(
        response({
          id: 'user-1',
          username: 'reader',
          is_admin: false,
          permissions: grantedPermissions('platform:work:use')
        })
      )
    if (path.startsWith('/api/work/tasks/task-1/runs')) {
      runtimeRequests.push(path)
      if (path.endsWith('/events')) return Promise.resolve(new Response('', { status: 503 }))
      return Promise.resolve(response(run()))
    }
    if (path === '/api/work/tasks/task-1')
      return Promise.resolve(
        response(
          rawTask({
            ...task('task-1', busy ? 1 : 2),
            ownerId: 'other-owner',
            currentRunId: busy ? 'run-1' : null
          })
        )
      )
    if (path.startsWith('/api/work/agents/available')) return Promise.resolve(response([]))
    throw new Error('Unexpected request ' + path)
  }
  await restoreIdentity(true)
  const workspace = mountedWorkspace(t)
  await workspace.loadTask('task-1')
  await flush()
  assert.equal(workspace.canStop, false)
  await workspace.cancelRun()
  await workspace.reconnect()
  assert.deepEqual(runtimeRequests, [])
  busy = false
  await workspace.loadTask('task-1')
  await flush()
  assert.equal(workspace.submissionBlocked, false)
  assert.equal(await workspace.runMessage({ agentId: 'agent-a', message: 'Foreign task' }), false)
  assert.deepEqual(runtimeRequests, [])
})

for (const status of [408, 503]) {
  void test(
    'unknown Work admission preserves the request after HTTP ' +
      status +
      ' and a rejected confirmation',
    async (t) => {
      const { resources, commands, select } = controllers(t)
      select(task())
      const posted: Record<string, unknown>[] = []
      fetcher = (input, init) => {
        const path =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
        if (path === '/api/work/tasks/task-1/runs' && init?.method === 'POST') {
          assert.ok(typeof init.body === 'string')
          posted.push(JSON.parse(init.body) as Record<string, unknown>)
          if (posted.length < 3)
            return Promise.resolve(
              new Response(
                JSON.stringify({
                  error: {
                    code: posted.length === 1 ? 'gateway_timeout' : 'forbidden',
                    message: 'Result unconfirmed'
                  }
                }),
                {
                  status: posted.length === 1 ? status : 403,
                  headers: { 'Content-Type': 'application/json' }
                }
              )
            )
          return Promise.resolve(response(run('completed')))
        }
        if (path === '/api/work/tasks/task-1')
          return Promise.resolve(response(rawTask(task('task-1', 9))))
        if (path.startsWith('/api/work/tasks?'))
          return Promise.resolve(response({ items: [], total: 0 }))
        throw new Error('Unexpected request ' + path)
      }
      assert.equal(commands.submissionBlocked.value, false)
      assert.equal(
        await commands.runMessage({ agentId: 'agent-a', message: 'Original Work request' }),
        false
      )
      assert.equal(commands.retryKind.value, 'uncertain')
      await resources.loadTask('task-1')
      assert.equal(resources.currentTask.value?.version, 9)
      assert.equal(
        await commands.runMessage({
          agentId: 'agent-b',
          message: 'Replacement must not overwrite'
        }),
        false
      )
      assert.equal(posted.length, 1)
      await commands.retryRun()
      assert.equal(commands.retryKind.value, 'uncertain')
      await commands.retryRun()
      assert.equal(posted.length, 3)
      assert.ok(posted.every((body) => JSON.stringify(body) === JSON.stringify(posted[0])))
      assert.equal(posted[0]?.expected_version, 1)
      assert.equal(posted[0]?.agent_id, 'agent-a')
      assert.equal(posted[0]?.message, 'Original Work request')
    }
  )
}
