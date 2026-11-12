import assert from 'node:assert/strict'
import { test } from 'node:test'
import { projectPreparation } from '../../src/modules/knowledge/preparation.ts'
import type { KnowledgePreparation } from '../../src/modules/knowledge/api/index.ts'
import { parseRunSnapshot } from '../../src/shared/stream.ts'

const preparation = (
  status: KnowledgePreparation['status'],
  overrides: Partial<KnowledgePreparation> = {}
): KnowledgePreparation => ({
  kind: 'knowledge_preparation',
  id: 'prep-1',
  resource_type: 'chat',
  resource_id: 'conversation-1',
  status,
  phase: status,
  run: null,
  hits: [],
  error: null,
  created_at: '2026-10-23T00:00:00Z',
  updated_at: '2026-10-23T00:00:00Z',
  ...overrides
})

void test('confirmed cancellation rejects a late waiting response and late admission', () => {
  const cancelled = preparation('cancelled')
  assert.equal(projectPreparation(cancelled, preparation('waiting_models')), cancelled)
  assert.equal(projectPreparation(cancelled, preparation('admitted')), cancelled)
})

void test('cancellation acknowledgement stays observable until its terminal result', () => {
  const acknowledged = preparation('cancel_requested')
  assert.equal(projectPreparation(acknowledged, preparation('searching')), acknowledged)
  assert.equal(projectPreparation(acknowledged, preparation('cancelled')).status, 'cancelled')
})

void test('older preparation responses cannot replace a newer observation', () => {
  const newer = preparation('searching', { updated_at: '2026-10-23T00:00:02Z' })
  assert.equal(projectPreparation(newer, preparation('waiting_models')), newer)
  assert.equal(
    projectPreparation(newer, preparation('queued', { id: 'new-intent' })).id,
    'new-intent'
  )
})

void test('admission revalidation refreshes sources while retaining the latest Run', () => {
  const run = parseRunSnapshot({
    id: 'run-1',
    resource_type: 'chat',
    resource_id: 'conversation-1',
    status: 'cancelled',
    attempt_id: 'attempt-1',
    attempt_no: 1,
    revision: 5,
    projection_seq: 1,
    text: '',
    error: null,
    created_at: '2026-10-23T00:00:00Z',
    usage: { input_tokens: 0, output_tokens: 0, status: 'known' }
  })
  const current = preparation('admitted', { run })
  const refreshed = preparation('admitted', { run: { ...run, status: 'running', revision: 3 } })
  const result = projectPreparation(current, refreshed)
  assert.equal(result.run, run)
  assert.equal(result.hits, refreshed.hits)
})
