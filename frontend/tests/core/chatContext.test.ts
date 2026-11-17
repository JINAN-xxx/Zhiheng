import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseContextInfo, contextDescription } from '../../src/modules/chat/contextInfo.ts'
import { parseRunSnapshot } from '../../src/shared/stream.ts'
import { creditSource } from '../../src/modules/billing/presentation.ts'
import { fullChatContext } from './chatContextFixture.ts'

const state = {
  ...fullChatContext,
  status: 'summarized',
  effective_memory: {
    summary_used: true,
    covered_rounds: 1,
    retained_rounds: 2,
    estimated_tokens: 500
  },
  last_run_context: {
    run_id: 'run-1',
    summary_prepared: true,
    estimated_input_tokens: 600,
    input_budget: 8192,
    summary_trigger_tokens: 6000,
    retained_token_budget: 2048
  }
}
void test('accepted memory and last execution retain separate context facts', () => {
  const parsed = parseContextInfo(state)
  assert.equal(parsed.effective_memory.estimated_tokens, 500)
  assert.equal(parsed.last_run_context?.estimated_input_tokens, 600)
  assert.equal(parsed.last_run_context?.summary_trigger_tokens, 6000)
  assert.equal(contextDescription(parsed), '已按 Token 预算整理较早对话')
  const failedBranch = parseContextInfo({
    ...fullChatContext,
    last_run_context: {
      ...state.last_run_context,
      estimated_input_tokens: null
    }
  })
  assert.equal(failedBranch.last_run_context?.summary_prepared, true)
  assert.equal(failedBranch.last_run_context?.estimated_input_tokens, null)
  assert.equal(failedBranch.effective_memory.summary_used, false)
  assert.equal(contextDescription(failedBranch), '')
  assert.match(
    contextDescription(parseContextInfo({ ...state, status: 'summarizing' })),
    /正在按 Token 预算整理会话摘要/
  )
})
void test('failed summaries retain accepted memory and never claim a truncated history', () => {
  const degraded = contextDescription(
    parseContextInfo({ ...state, status: 'degraded', reason: 'provider_unavailable' })
  )
  assert.match(degraded, /已保留原有效记忆/)
  assert.match(degraded, /请重新发送问题或重新生成回复/)
  assert.doesNotMatch(degraded, /provider_unavailable|8192|部分较早对话未带入/)
  assert.match(
    contextDescription(
      parseContextInfo({ ...state, status: 'degraded', reason: 'chat_checkpoint_missing' })
    ),
    /请新建对话/
  )
})
void test('context accepts unknown actual input and rejects obsolete or malformed projections', () => {
  assert.equal(parseContextInfo(fullChatContext).last_run_context, null)
  assert.equal(
    parseContextInfo({
      ...state,
      last_run_context: { ...state.last_run_context, input_budget: null }
    }).last_run_context?.input_budget,
    null
  )
  for (const status of ['unknown', ['full'], null])
    assert.throws(() => parseContextInfo({ ...state, status }))
  for (const strategy of ['rounds', 'unknown', null, undefined])
    assert.throws(() => parseContextInfo({ ...state, strategy }))
  assert.throws(() => parseContextInfo({ ...state, effective_memory: undefined }))
  assert.throws(() => parseContextInfo({ ...state, last_run_context: undefined }))
  assert.throws(() =>
    parseContextInfo({
      ...state,
      last_run_context: { ...state.last_run_context, input_budget: '8192' }
    })
  )
  assert.throws(() =>
    parseContextInfo({
      ...state,
      effective_memory: { ...state.effective_memory, estimated_tokens: Infinity }
    })
  )
})

void test('summary usage and run snapshots keep the native source contract', () => {
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
