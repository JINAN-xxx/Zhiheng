import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  parseContextInfo,
  contextDescription,
  nextContextRetryIntent
} from '../../src/modules/chat/contextInfo.ts'
import { parseRunSnapshot } from '../../src/shared/stream.ts'
import { creditSource } from '../../src/modules/billing/presentation.ts'

const state = {
  status: 'summarized',
  summary_version: 1,
  covered_rounds: 15,
  retained_rounds: 15,
  dirty: false,
  truncated: false,
  dropped_rounds: 0,
  estimated_input_tokens: 500,
  input_budget: 8192,
  reason: null
}
void test('context state preserves degraded/truncated facts and uses concise product copy', () => {
  assert.equal(contextDescription(parseContextInfo(state)), '已整理较早对话')
  const degraded = parseContextInfo({
    ...state,
    status: 'degraded',
    truncated: true,
    dropped_rounds: 4,
    reason: 'quota_exceeded'
  })
  assert.match(contextDescription(degraded), /部分较早对话未带入/)
  assert.doesNotMatch(contextDescription(degraded), /quota_exceeded|8192/)
  assert.throws(() => parseContextInfo({ ...state, status: 'unknown' }))
})
void test('durable dirty state stays visibly processing before a summary Run exists', () => {
  assert.match(
    contextDescription(parseContextInfo({ ...state, status: 'summarizing', dirty: true })),
    /正在整理/
  )
})
void test('unconfirmed context retry reuses the same owner and conversation request identity', () => {
  const first = nextContextRetryIntent(null, 'u1', 'c1', () => 'first')
  assert.equal(
    nextContextRetryIntent(first, 'u1', 'c1', () => 'other'),
    first
  )
  assert.notEqual(
    nextContextRetryIntent(first, 'u1', 'c2', () => 'second').requestId,
    first.requestId
  )
  assert.notEqual(
    nextContextRetryIntent(first, 'u2', 'c1', () => 'third').requestId,
    first.requestId
  )
})
void test('summary usage and run snapshots use the native source contract', () => {
  assert.equal(creditSource('chat_summary'), '会话摘要')
  assert.equal(
    parseRunSnapshot({
      id: 's1',
      resource_type: 'chat_summary',
      resource_id: 'task1',
      status: 'completed',
      attempt_id: 'a1',
      attempt_no: 1,
      revision: 1,
      projection_seq: 1,
      text: '摘要',
      error: null,
      created_at: '2026-11-03T00:00:00Z',
      usage: { input_tokens: 8, output_tokens: 2, status: 'known' }
    }).resource_type,
    'chat_summary'
  )
})

void test('a plain full context stays silent even if an older server exposes its detection marker', () => {
  assert.equal(
    contextDescription(
      parseContextInfo({ ...state, status: 'full', summary_version: 0, dirty: true })
    ),
    ''
  )
})
