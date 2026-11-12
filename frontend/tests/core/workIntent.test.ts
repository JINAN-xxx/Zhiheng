import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  nextCreateIntent,
  nextWorkIntent,
  validateWorkGoal
} from '../../src/modules/work/presentation.ts'

void test('unknown Work admission retains key and original CAS when detail changes', () => {
  let ids = 0
  const input = {
    taskId: 'task-1',
    agentId: 'agent-a',
    message: '  research  ',
    expectedVersion: 2
  }
  const first = nextWorkIntent(null, input, () => `intent-${++ids}`)
  const retry = nextWorkIntent(first, { ...input, expectedVersion: 3 }, () => `intent-${++ids}`)
  assert.equal(retry.clientRequestId, first.clientRequestId)
  assert.equal(retry.expectedVersion, 2)
  assert.equal(first.message, 'research')
  assert.notEqual(
    nextWorkIntent(first, { ...input, agentId: 'agent-b' }, () => `intent-${++ids}`)
      .clientRequestId,
    first.clientRequestId
  )
  assert.notEqual(
    nextWorkIntent(first, { ...input, taskId: 'task-2' }, () => `intent-${++ids}`).clientRequestId,
    first.clientRequestId
  )
})
void test('reordered creation members preserve intent but changed executor does not', () => {
  let ids = 0
  const draft = { goal: 'goal', agentIds: ['a', 'b'], activeAgentId: 'a' }
  const first = nextCreateIntent(null, draft, () => `intent-${++ids}`)
  draft.agentIds.push('c')
  assert.deepEqual(first.agentIds, ['a', 'b'])
  assert.equal(
    nextCreateIntent(first, { ...draft, agentIds: ['b', 'a'] }, () => `intent-${++ids}`)
      .clientRequestId,
    first.clientRequestId
  )
  assert.notEqual(
    nextCreateIntent(first, { ...draft, activeAgentId: 'b' }, () => `intent-${++ids}`)
      .clientRequestId,
    first.clientRequestId
  )
})
void test('Work goals enforce real UTF8 context limits and valid persisted Unicode', () => {
  assert.equal(validateWorkGoal('中文'.repeat(11000)).valid, false)
  assert.equal(validateWorkGoal('a\u0000b').valid, false)
  assert.equal(validateWorkGoal('\ud800').valid, false)
  assert.equal(validateWorkGoal('  请调查  ').valid, true)
})
