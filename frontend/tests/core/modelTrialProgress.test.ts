import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseTrialProgress, trialPayload } from '../../src/modules/models/trialProgress.ts'

const requestId = '3f2d9920-8019-4ae4-891e-508c229f2511'
const runId = '2e432844-8150-47ad-a6c1-591020b5066d'
void test('trial input is bounded and rerank candidates retain their order', () => {
  assert.deepEqual(trialPayload(' hello ', '', 'chat'), { prompt: 'hello', documents: [] })
  assert.deepEqual(trialPayload(' question ', ' first \n\n second ', 'rerank'), {
    prompt: 'question',
    documents: ['first', 'second']
  })
  for (const [prompt, docs, kind] of [
    ['', '', 'chat'],
    ['x'.repeat(4001), '', 'chat'],
    ['question', '', 'rerank'],
    ['question', Array(9).fill('candidate').join('\n'), 'rerank'],
    ['question', 'x'.repeat(1001), 'rerank']
  ] as const)
    assert.throws(() => trialPayload(prompt, docs, kind))
})
void test('pending trial restoration binds the exact input to its account and model', () => {
  const progress = {
    ownerId: 'owner',
    modelId: 'model',
    runId,
    intent: { client_request_id: requestId, prompt: 'original question', documents: ['one', 'two'] }
  }
  const raw = JSON.stringify(progress)
  assert.deepEqual(parseTrialProgress(raw, 'owner', 'model'), progress)
  assert.equal(parseTrialProgress(raw, 'different-owner', 'model'), null)
  assert.equal(parseTrialProgress(raw, 'owner', 'different-model'), null)
  assert.equal(parseTrialProgress('{broken', 'owner', 'model'), null)
  assert.equal(
    parseTrialProgress(
      JSON.stringify({ ...progress, runId: 'untrusted-path/other-run' }),
      'owner',
      'model'
    ),
    null
  )
  assert.equal(
    parseTrialProgress(
      JSON.stringify({
        ...progress,
        intent: { ...progress.intent, documents: ['x'.repeat(1001)] }
      }),
      'owner',
      'model'
    ),
    null
  )
})
