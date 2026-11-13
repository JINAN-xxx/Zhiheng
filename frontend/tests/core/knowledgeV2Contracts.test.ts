import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseGraphJob } from '../../src/modules/knowledge/api/graph.ts'
import { parsePreparation } from '../../src/modules/knowledge/api/index.ts'

const snapshot = (diagnostics: unknown) => ({
  kind: 'knowledge_preparation',
  id: 'prep',
  resource_type: 'knowledge_query',
  resource_id: 'base',
  status: 'completed',
  phase: 'completed',
  run: null,
  hits: [],
  error: null,
  created_at: '2026-11-06T00:00:00Z',
  updated_at: '2026-11-06T00:00:00Z',
  diagnostics
})
void test('empty management diagnostics are valid for readers and restored legacy preparations', () => {
  assert.equal(parsePreparation(snapshot({})).diagnostics, null)
})
void test('historical fallback remains readable and distinct from a completed rerank', () => {
  const diagnostics = {
    candidate_count: 17,
    retrieval_modes: { base: 'hybrid' },
    rerank_status: 'fallback',
    rerank_reason: 'temporary_timeout'
  }
  const parsed = parsePreparation(snapshot(diagnostics)).diagnostics
  assert.equal(parsed?.candidate_count, 17)
  assert.equal(parsed?.rerank_status, 'fallback')
  assert.equal(parsed?.rerank_milliseconds, undefined)
})

void test('no candidates are returned without inventing a rerank result', () => {
  const parsed = parsePreparation(
    snapshot({
      candidate_count: 0,
      retrieval_modes: { base: 'hybrid' },
      rerank_status: 'no_candidates'
    })
  )
  assert.equal(parsed.diagnostics?.rerank_status, 'no_candidates')
})
void test('restored graph query preserves actual retrieval diagnostics', () => {
  const parsed = parseGraphJob({
    id: 'graph-job',
    base_id: 'base',
    operation: 'query',
    status: 'completed',
    phase: 'completed',
    version: 1,
    attempt_id: 'attempt',
    graph_revision: 'revision',
    processed_documents: null,
    total_documents: null,
    progress: { extracted_chunks: null, total_chunks: null, entities: null, relations: null },
    error: null,
    cancel_allowed: false,
    created_at: 'stamp',
    updated_at: 'stamp',
    finished_at: 'stamp',
    result: {
      context: 'graph context',
      entities: [],
      relationships: [],
      sources: [],
      diagnostics: {
        candidate_count: 4,
        retrieval_modes: { base: 'hybrid' },
        rerank_status: 'completed',
        rerank_milliseconds: 12
      }
    }
  })
  assert.equal(
    parsed.result &&
      'context' in parsed.result &&
      'diagnostics' in parsed.result &&
      (parsed.result.diagnostics as { candidate_count: number }).candidate_count,
    4
  )
})

void test('historical graph diagnostics preserve status when recall metrics were not recorded', () => {
  const parsed = parseGraphJob({
    id: 'graph-job',
    base_id: 'base',
    operation: 'query',
    status: 'completed',
    phase: 'completed',
    version: 1,
    attempt_id: 'attempt',
    graph_revision: 'revision',
    processed_documents: null,
    total_documents: null,
    progress: { extracted_chunks: null, total_chunks: null, entities: null, relations: null },
    error: null,
    cancel_allowed: false,
    created_at: 'stamp',
    updated_at: 'stamp',
    finished_at: 'stamp',
    result: {
      context: 'graph context',
      entities: [],
      relationships: [],
      sources: [],
      diagnostics: { rerank_status: 'not_configured' }
    }
  })
  assert.equal(
    parsed.result && 'context' in parsed.result && parsed.result.diagnostics?.candidate_count,
    null
  )
  assert.equal(
    parsed.result && 'context' in parsed.result && parsed.result.diagnostics?.rerank_status,
    'not_configured'
  )
})

void test('rerank failure diagnostics preserve the server error instead of becoming an unknown state', () => {
  const value = {
    ...snapshot({
      candidate_count: 2,
      retrieval_modes: { base: 'hybrid' },
      rerank_status: 'failed',
      rerank_reason: 'knowledge_rerank_transient'
    }),
    status: 'failed',
    phase: 'failed',
    error: { code: 'knowledge_rerank_failed', message: '重排失败，本次未返回检索结果' }
  }
  const parsed = parsePreparation(value)
  assert.equal(parsed.status, 'failed')
  assert.equal(parsed.diagnostics?.rerank_status, 'failed')
  assert.equal(parsed.error?.code, 'knowledge_rerank_failed')
  assert.deepEqual(parsed.hits, [])
})
