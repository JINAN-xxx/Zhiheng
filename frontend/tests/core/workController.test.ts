import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { createRenderer, defineComponent, h } from 'vue'
import type { WorkTask } from '../../src/modules/work/api/index.ts'
import type { RunSnapshot } from '../../src/shared/stream.ts'
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
    mode: 'agent',
    modelId: 'model-a',
    executionState: 'idle',
    configuration: { modelId: 'model-a', skillSlugs: [], allowCodeExecution: false },
    inputs: [],
    steps: [],
    artifacts: [],
    plan: [],
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
    mode: value.mode,
    model_id: value.modelId,
    execution_state: value.executionState,
    configuration: value.configuration && {
      model_id: value.configuration.modelId,
      skill_slugs: value.configuration.skillSlugs,
      allow_code_execution: value.configuration.allowCodeExecution
    },
    inputs: value.inputs.map((input) => ({
      file_id: input.fileId,
      name: input.name,
      version: input.version,
      size: input.size,
      mime: input.mime,
      sha256: input.sha256,
      acl_version: input.aclVersion,
      file_version: input.fileVersion,
      created_at: input.createdAt
    })),
    steps: [],
    artifacts: [],
    plan: [],
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
    resource_type: 'work_agent',
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
    runSettled: (value) => commands.runSettled(value)
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
    if (path === '/api/work/options')
      return Promise.resolve(
        response({ models: [], skills: [], can_execute_code: false, sandbox_available: false })
      )
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
    if (path === '/api/work/options')
      return Promise.resolve(
        response({ models: [], skills: [], can_execute_code: false, sandbox_available: false })
      )
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
  assert.equal(await workspace.runMessage({ message: 'Forbidden new turn' }), false)
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
    if (path === '/api/work/options')
      return Promise.resolve(
        response({ models: [], skills: [], can_execute_code: false, sandbox_available: false })
      )
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
  assert.equal(await workspace.runMessage({ message: 'Foreign task' }), false)
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
      assert.equal(await commands.runMessage({ message: 'Original Work request' }), false)
      assert.equal(commands.retryKind.value, 'uncertain')
      await resources.loadTask('task-1')
      assert.equal(resources.currentTask.value?.version, 9)
      assert.equal(
        await commands.runMessage({
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
      assert.deepEqual(posted[0]?.file_ids, [])
      assert.equal(posted[0]?.resume, false)
      assert.equal(posted[0]?.message, 'Original Work request')
    }
  )
}

void test('upload-first draft applies refreshed versions and binds files without admitting a model run', async (t) => {
  const { resources, commands, select } = controllers(t)
  const draft = { ...task(), goal: '', title: '新任务' }
  select(draft)
  const uploads: string[] = []
  fetcher = (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path.startsWith('/api/work/tasks/task-1/files?')) {
      assert.equal(init?.method, 'POST')
      assert.ok(init.body instanceof File)
      uploads.push(init.body.name)
      const value = {
        ...draft,
        version: 1 + uploads.length,
        inputs: uploads.map((name, index) => ({
          fileId: 'file-' + index,
          name,
          version: 1,
          size: 1,
          mime: 'text/plain',
          sha256: 'a'.repeat(64),
          aclVersion: 1,
          fileVersion: 1,
          createdAt: stamp
        }))
      }
      return Promise.resolve(response(rawTask(value)))
    }
    if (path.startsWith('/api/work/tasks?'))
      return Promise.resolve(response({ items: [], total: 0 }))
    throw new Error('Unexpected request ' + path)
  }
  await commands.uploadFiles([new File(['a'], 'first.txt'), new File(['b'], 'second.txt')])
  assert.deepEqual(uploads, ['first.txt', 'second.txt'])
  assert.equal(resources.currentTask.value?.version, 3)
  assert.equal(resources.currentTask.value?.inputs.length, 2)
  assert.equal(commands.uploading.value, false)
})

void test('switching tasks ends an upload batch before another file or stale projection', async (t) => {
  const { resources, commands, select } = controllers(t)
  select(task())
  const held = deferred<Response>(),
    uploads: string[] = []
  fetcher = (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (init?.method === 'POST') {
      uploads.push(path)
      return held.promise
    }
    throw new Error('Unexpected request ' + path)
  }
  const upload = commands.uploadFiles([new File(['a'], 'a.txt'), new File(['b'], 'b.txt')])
  select(task('task-2'))
  held.resolve(response(rawTask(task('task-1', 2))))
  await upload
  assert.equal(uploads.length, 1)
  assert.equal(resources.currentTask.value?.taskId, 'task-2')
})

void test('legacy history never admits, mutates or starts sandbox file upload', async (t) => {
  const { commands, observation, select } = controllers(t)
  select({ ...task(), mode: 'legacy', currentRunId: 'old-run' })
  let calls = 0
  fetcher = () => {
    calls++
    throw new Error('Historical tasks are read-only')
  }
  assert.equal(await commands.runMessage({ message: 'do work' }), false)
  assert.equal(await commands.saveTask({ title: 'changed' }), false)
  await commands.uploadFiles([new File(['a'], 'a.txt')])
  await observation.cancelRun()
  assert.equal(calls, 0)
})

void test('task decoding retains real bounded tool results separately from execution arguments', async (t) => {
  const { resources } = controllers(t)
  fetcher = () =>
    Promise.resolve(
      response({
        ...rawTask(task()),
        steps: [
          {
            id: 'step-1',
            run_id: 'run-1',
            action_id: 'a',
            kind: 'tool',
            name: 'run_python',
            status: 'succeeded',
            summary: '核对完成',
            details: { arguments: { code: 'print(42)' }, arguments_fingerprint: 'internal' },
            result: {
              success: true,
              data: { stdout: '真实结果42', stderr: '' },
              _workspace_files: [{ file_id: 'internal-snapshot' }]
            },
            created_at: stamp,
            updated_at: stamp
          }
        ]
      })
    )
  await resources.loadTask('task-1')
  assert.deepEqual(resources.currentTask.value?.steps[0]?.result?.data, {
    stdout: '真实结果42',
    stderr: ''
  })
  assert.equal(resources.currentTask.value?.steps[0]?.details.arguments_fingerprint, 'internal')
})

void test('unknown task creation confirms its frozen goal, model and skills before another draft', async (t) => {
  const { commands } = controllers(t)
  const posted: Record<string, unknown>[] = []
  fetcher = (input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (path === '/api/work/tasks' && init?.method === 'POST') {
      assert.equal(typeof init.body, 'string')
      const body = JSON.parse(init.body as string) as Record<string, unknown>
      posted.push(body)
      if (posted.length === 1)
        return Promise.resolve(
          new Response(
            JSON.stringify({ error: { code: 'unavailable', message: 'unknown create' } }),
            { status: 503 }
          )
        )
      return Promise.resolve(
        response(
          rawTask({
            ...task(),
            goal: 'original',
            createClientRequestId: String(body.client_request_id)
          })
        )
      )
    }
    if (path.startsWith('/api/work/tasks?'))
      return Promise.resolve(response({ items: [], total: 0 }))
    throw new Error('Unexpected request ' + path)
  }
  await assert.rejects(
    commands.createTask({
      goal: 'original',
      modelId: 'model-a',
      skillSlugs: ['documents'],
      allowCodeExecution: false
    })
  )
  assert.equal(commands.createUncertain.value, true)
  await commands.createTask({
    goal: 'replacement',
    modelId: 'model-b',
    skillSlugs: [],
    allowCodeExecution: true
  })
  assert.deepEqual(posted[1], posted[0])
  assert.equal(posted[0]?.model_id, 'model-a')
  assert.deepEqual(posted[0]?.skill_slugs, ['documents'])
  assert.equal(posted[0]?.allow_code_execution, false)
  assert.equal(commands.createUncertain.value, false)
})
