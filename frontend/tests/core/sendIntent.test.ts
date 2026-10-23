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
