import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  nextCreateIntent,
  nextWorkIntent,
  validateWorkGoal
} from '../../src/modules/work/presentation.ts'

void test('unknown Agent admission retains key, files and original CAS when detail changes', () => {
  let ids = 0
  const input = {
    taskId: 'task-1',
    message: '  research  ',
    expectedVersion: 2,
    fileIds: ['a', 'b'],
    resume: false
  }
  const first = nextWorkIntent(null, input, () => `intent-${++ids}`)
  input.fileIds.push('later')
  assert.deepEqual(first.fileIds, ['a', 'b'])
  const retry = nextWorkIntent(
    first,
    { ...input, fileIds: ['b', 'a'], expectedVersion: 3 },
    () => `intent-${++ids}`
  )
  assert.equal(retry.clientRequestId, first.clientRequestId)
  assert.equal(retry.expectedVersion, 2)
  assert.equal(retry.message, 'research')
  assert.notEqual(
    nextWorkIntent(first, { ...input, taskId: 'task-2' }, () => `intent-${++ids}`).clientRequestId,
    first.clientRequestId
  )
  assert.notEqual(
    nextWorkIntent(first, { ...input, resume: true }, () => `intent-${++ids}`).clientRequestId,
    first.clientRequestId
  )
})
void test('creation intent freezes model, skill selection and code execution grant', () => {
  let ids = 0
  const draft = {
    goal: 'goal',
    modelId: 'model-a',
    skillSlugs: ['a', 'b'],
    allowCodeExecution: false
  }
  const first = nextCreateIntent(null, draft, () => `intent-${++ids}`)
  draft.skillSlugs.push('later')
  assert.deepEqual(first.skillSlugs, ['a', 'b'])
  assert.equal(
    nextCreateIntent(first, { ...draft, skillSlugs: ['b', 'a'] }, () => `intent-${++ids}`)
      .clientRequestId,
    first.clientRequestId
  )
  assert.notEqual(
    nextCreateIntent(first, { ...draft, modelId: 'model-b' }, () => `intent-${++ids}`)
      .clientRequestId,
    first.clientRequestId
  )
  assert.notEqual(
    nextCreateIntent(first, { ...draft, allowCodeExecution: true }, () => `intent-${++ids}`)
      .clientRequestId,
    first.clientRequestId
  )
})
void test('Work goals retain the platform UTF8 and persisted Unicode limits', () => {
  assert.equal(validateWorkGoal('中文'.repeat(11000)).valid, false)
  assert.equal(validateWorkGoal('a\u0000b').valid, false)
  assert.equal(validateWorkGoal('\ud800').valid, false)
  assert.equal(validateWorkGoal('  请调查  ').valid, true)
})
