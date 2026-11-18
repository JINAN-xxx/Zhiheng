import assert from 'node:assert/strict'
import { after, test } from 'node:test'

const originalFetch = globalThis.fetch
let usage: Record<string, unknown> = { status: 'pending' }
const stamp = '2026-11-18T00:00:00Z'
globalThis.fetch = () =>
  Promise.resolve(
    new Response(
      JSON.stringify({
        id: 'task-1',
        owner_id: 'user-1',
        title: '绘图任务',
        goal: '绘图',
        status: 'active',
        mode: 'agent',
        model_id: 'model-1',
        execution_state: 'running',
        version: 1,
        current_run_id: 'run-1',
        create_client_request_id: 'create-1',
        created_at: stamp,
        updated_at: stamp,
        configuration: { model_id: 'model-1', skill_slugs: [] },
        messages: [
          {
            id: 'message-1',
            client_request_id: 'request-1',
            role: 'assistant',
            content: '',
            status: 'running',
            run_id: 'run-1',
            sequence: 1,
            created_at: stamp,
            usage
          }
        ]
      }),
      { headers: { 'Content-Type': 'application/json' } }
    )
  )

after(() => {
  globalThis.fetch = originalFetch
})
const { getTask } = await import('../../src/modules/work/api/index.ts')

for (const value of [
  {
    label: 'pending counters not yet reported',
    usage: { status: 'pending' },
    expected: [null, null]
  },
  {
    label: 'pending input counter already reported',
    usage: { status: 'pending', input_tokens: 12 },
    expected: [12, null]
  },
  {
    label: 'unknown counters explicitly null',
    usage: { status: 'unknown', input_tokens: null, output_tokens: null },
    expected: [null, null]
  },
  {
    label: 'confirmed zero is preserved',
    usage: { status: 'known', input_tokens: 0, output_tokens: 0 },
    expected: [0, 0]
  }
]) {
  void test(`running workspace detail accepts ${value.label}`, async () => {
    usage = value.usage
    const task = await getTask('task-1', new AbortController().signal)
    assert.equal(task.executionState, 'running')
    assert.deepEqual(
      [task.messages[0]?.usage?.input_tokens, task.messages[0]?.usage?.output_tokens],
      value.expected
    )
    assert.equal(task.messages[0]?.usage?.status, value.usage.status)
  })
}
void test('workspace message counters still reject numeric strings', async () => {
  usage = { status: 'known', input_tokens: '12', output_tokens: 2 }
  await assert.rejects(
    getTask('task-1', new AbortController().signal),
    /服务器返回的数值格式不正确/
  )
})
