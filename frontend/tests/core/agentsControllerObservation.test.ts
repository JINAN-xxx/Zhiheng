import assert from 'node:assert/strict'
import { after, test } from 'node:test'
import type { TestContext } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { ref } from 'vue'
import type {
  AssistantConversation,
  AssistantTurn
} from '../../src/modules/agents/api/conversations.ts'
import type { AssistantOutgoingTurn } from '../../src/modules/agents/composables/useAssistantCommands.ts'
import type { KnowledgePreparation } from '../../src/modules/knowledge/public.ts'
import type { RunSnapshot } from '../../src/shared/stream.ts'

const originalFetch = globalThis.fetch
let fetcher: typeof fetch = originalFetch
globalThis.fetch = (...args) => fetcher(...args)
const { RequestScope } = await import('../../src/shared/http.ts')
const { useAssistantRunObservation } = await import(
  '../../src/modules/agents/composables/useAssistantRunObservation.ts'
)
after(() => {
  globalThis.fetch = originalFetch
})

const stamp = '2026-11-15T00:00:00Z'
const conversation = (id: string): AssistantConversation => ({
  id,
  agentId: 'agent-1',
  title: id,
  name: '助手',
  description: '',
  welcomeMessage: '',
  version: 1,
  createdAt: stamp,
  updatedAt: stamp,
  memoryAvailable: true,
  summaryUsed: false,
  preparationId: null
})
const run = (status: RunSnapshot['status'] = 'running', revision = 1): RunSnapshot => ({
  id: 'run-1',
  resource_type: 'agent_chat',
  resource_id: 'c1',
  status,
  revision,
  projection_seq: revision,
  attempt_id: 'attempt-1',
  attempt_no: 1,
  text: '',
  error: null,
  created_at: stamp,
  usage: { input_tokens: null, output_tokens: null, status: 'pending' }
})
const preparation = (
  status: KnowledgePreparation['status'] = 'waiting_models',
  admitted: RunSnapshot | null = null
): KnowledgePreparation => ({
  kind: 'knowledge_preparation',
  id: 'prep-1',
  resource_type: 'agent_chat',
  resource_id: 'c1',
  status,
  phase: 'embedding',
  run: admitted,
  hits: [],
  error: null,
  created_at: stamp,
  updated_at: stamp
})
const turn = (snapshot: RunSnapshot): AssistantTurn => ({
  id: 'turn-1',
  requestId: 'request-1',
  sequence: 1,
  agentName: '助手',
  content: '问题',
  attachments: [],
  run: snapshot,
  sources: []
})
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
function fixture(t: TestContext) {
  const scope = new RequestScope()
  t.after(() => scope.dispose())
  const selected = ref<AssistantConversation | null>(conversation('c1'))
  const turns = ref<AssistantTurn[]>([])
  const outgoing = ref<AssistantOutgoingTurn | null>({
    requestId: 'request-1',
    conversationId: 'c1',
    content: '问题',
    attachments: [],
    stage: 'preparing',
    runId: null,
    error: ''
  })
  const error = ref('')
  const detailReads: string[] = []
  const observation = useAssistantRunObservation({
    selected,
    turns,
    outgoing,
    conversationScope: scope,
    error,
    readDetail: (id) => {
      detailReads.push(id)
      return Promise.resolve(selected.value ?? undefined)
    },
    refreshList: async () => {},
    scroll: async () => {}
  })
  return { scope, selected, turns, outgoing, error, observation, detailReads }
}

void test('a preparation stop cancels an admitted run once while a stale poll is in flight', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const state = fixture(t)
  const poll = deferred<Response>()
  const cancellation = deferred<Response>()
  const paths: string[] = []
  fetcher = async (input) => {
    const path = new URL(input instanceof Request ? input.url : input, 'http://fixture').pathname
    paths.push(path)
    if (path === '/api/knowledge/preparations/prep-1') return poll.promise
    if (path === '/api/knowledge/preparations/prep-1/cancel') return cancellation.promise
    if (path === '/api/runs/run-1/cancel') return response(run('cancelled', 3))
    throw new Error('Unexpected request: ' + path)
  }
  const following = state.observation.followKnowledge(preparation(), state.scope.current())
  t.mock.timers.tick(1500)
  await flush()
  const stopping = state.observation.stop()
  assert.equal(state.observation.stop(), stopping)
  cancellation.resolve(response(preparation('admitted', run())))
  await stopping
  poll.resolve(response(preparation()))
  await following
  assert.equal(state.observation.activeRun.value?.status, 'cancelled')
  assert.equal(state.observation.running.value, false)
  assert.equal(paths.filter((path) => path.endsWith('/prep-1/cancel')).length, 1)
  assert.equal(paths.filter((path) => path.endsWith('/run-1/cancel')).length, 1)
  assert.deepEqual(state.detailReads, ['c1', 'c1'])
})

void test('a stale preparation poll cannot undo an acknowledged cancellation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const state = fixture(t)
  const poll = deferred<Response>()
  fetcher = async (input) => {
    const path = new URL(input instanceof Request ? input.url : input, 'http://fixture').pathname
    if (path.endsWith('/cancel')) return response(preparation('cancelled'))
    return poll.promise
  }
  const following = state.observation.followKnowledge(preparation(), state.scope.current())
  t.mock.timers.tick(1500)
  await flush()
  await state.observation.stop()
  poll.resolve(response(preparation()))
  await following
  assert.equal(state.observation.preparation.value?.status, 'cancelled')
  assert.equal(state.outgoing.value?.stage, 'cancelled')
  assert.equal(state.observation.running.value, false)
})

void test('a lost preparation stop remains pending when recovery discovers its admitted run', async (t) => {
  const state = fixture(t)
  const paths: string[] = []
  fetcher = (input) => {
    const path = new URL(input instanceof Request ? input.url : input, 'http://fixture').pathname
    paths.push(path)
    if (path.includes('/knowledge/'))
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'unavailable', message: '取消确认丢失' } }), {
          status: 503
        })
      )
    if (path === '/api/runs/run-1/cancel') return Promise.resolve(response(run('cancelled', 3)))
    throw new Error('Unexpected request: ' + path)
  }
  state.observation.preparation.value = preparation()
  await state.observation.stop()
  assert.equal(state.observation.disconnected.value, true)
  assert.equal(state.error.value, '取消确认丢失')
  await state.observation.followKnowledge(preparation('admitted', run()), state.scope.replace())
  assert.equal(state.observation.activeRun.value?.status, 'cancelled')
  assert.equal(paths.filter((path) => path.endsWith('/run-1/cancel')).length, 1)
})

void test('switching conversations aborts a pending stop and rejects its late response', async (t) => {
  const state = fixture(t)
  const cancellation = deferred<Response>()
  let oldSignal: AbortSignal | null | undefined
  fetcher = async (_input, init) => {
    oldSignal = init?.signal
    return cancellation.promise
  }
  state.observation.activeRun.value = run()
  const stopping = state.observation.stop()
  state.scope.replace()
  state.observation.reset()
  state.selected.value = conversation('c2')
  const second = { ...run(), id: 'run-2', resource_id: 'c2' }
  state.observation.project(second)
  cancellation.resolve(response(run('cancelled', 3)))
  await stopping
  assert.equal(oldSignal?.aborted, true)
  assert.equal(state.observation.activeRun.value?.id, 'run-2')
  assert.equal(state.observation.activeRun.value?.status, 'running')
  assert.equal(state.error.value, '')
})

void test('conversation detail cannot replace a newer cancelled run with an older running snapshot', async (t) => {
  const state = fixture(t)
  state.observation.project(run('cancelled', 5))
  state.observation.restoreTurns([turn(run())])
  assert.equal(state.turns.value[0]?.run.status, 'cancelled')
  assert.equal(state.observation.activeRun.value?.revision, 5)
  await state.observation.follow(run(), state.scope.current())
  assert.equal(state.observation.running.value, false)
})

void test('run ownership is checked before observing or cancelling it', (t) => {
  const state = fixture(t)
  assert.throws(() => state.observation.project({ ...run(), resource_id: 'c2' }), /当前对话不匹配/)
  assert.equal(state.observation.activeRun.value, null)
})
