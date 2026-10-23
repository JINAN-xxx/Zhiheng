import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextSendIntent } from '../../src/modules/chat/api/sendIntent.ts'
void test('same logical message retains the idempotency key after a lost response', () => {
  let ids = 0
  const createId = () => `request-${++ids}`
  const input = { conversationId: 'c1', model_id: 'm1', message: 'hello' }
  const first = nextSendIntent(null, input, createId)
  const retry = nextSendIntent(first, input, createId)
  assert.equal(first.client_request_id, retry.client_request_id)
  assert.equal(ids, 1)
  assert.notEqual(
    nextSendIntent(first, { ...input, conversationId: 'c2' }, createId).client_request_id,
    first.client_request_id
  )
  assert.notEqual(
    nextSendIntent(first, { ...input, message: 'different' }, createId).client_request_id,
    first.client_request_id
  )
})
void test('skill selection is frozen and ordered as part of the logical message', () => {
  let ids = 0
  const createId = () => `request-${++ids}`
  const selection = ['deep-research', 'document-summary']
  const input = { conversationId: 'c1', model_id: 'm1', message: 'hello', skill_slugs: selection }
  const first = nextSendIntent(null, input, createId)
  selection.reverse()
  assert.notEqual(nextSendIntent(first, input, createId).client_request_id, first.client_request_id)
  assert.deepEqual(first.skill_slugs, ['deep-research', 'document-summary'])
  assert.equal(
    nextSendIntent(
      first,
      { ...input, skill_slugs: ['deep-research', 'document-summary'] },
      createId
    ).client_request_id,
    first.client_request_id
  )
})
