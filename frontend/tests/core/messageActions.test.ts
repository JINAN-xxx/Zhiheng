import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseMessage } from '../../src/modules/chat/api/index.ts'
import {
  parseFeedback,
  parseSharePreview,
  parseMessageShare,
  parseSharedMessage
} from '../../src/modules/chat/api/messageActions.ts'
import {
  groupMessageVersions,
  nextRegenerationIntent
} from '../../src/modules/chat/composables/messageVersions.ts'

const message = (id: string, role: 'user' | 'assistant', extra: Record<string, unknown> = {}) =>
  parseMessage({
    id,
    role,
    content: id,
    run_id: null,
    created_at: '2026-10-28T00:00:00Z',
    ...extra
  })

void test('legacy message fixtures default action metadata but malformed new fields fail', () => {
  const legacy = message('a1', 'assistant')
  assert.equal(legacy.reply_to_message_id, null)
  assert.equal(legacy.generation_index, 1)
  assert.equal(legacy.is_complete, true)
  assert.equal(legacy.feedback, null)
  for (const extra of [
    { generation_index: 0 },
    { generation_index: 1.5 },
    { is_complete: 'yes' },
    { feedback: 'both' },
    { reply_to_message_id: 42 }
  ])
    assert.throws(() => message('bad', 'assistant', extra))
})

void test('reply versions remain in their original turn and legacy replies use the preceding question', () => {
  const rows = groupMessageVersions([
    message('q1', 'user'),
    message('a1', 'assistant'),
    message('q2', 'user'),
    message('a2', 'assistant', { reply_to_message_id: 'q2' }),
    message('a3', 'assistant', { reply_to_message_id: 'q2', generation_index: 2 })
  ])
  assert.deepEqual(
    rows.map((row) => row.versions.map((item) => item.id)),
    [['q1'], ['a1'], ['q2'], ['a2', 'a3']]
  )
  assert.equal(rows.at(-1)?.questionId, 'q2')
})

void test('unknown regeneration retains its request key and cannot be repurposed for another reply', () => {
  let count = 0
  const first = nextRegenerationIntent(null, 'c1', message('a1', 'assistant'), () => `r${++count}`)
  assert.equal(
    nextRegenerationIntent(first, 'c1', message('a1', 'assistant'), () => `r${++count}`),
    first
  )
  assert.throws(() =>
    nextRegenerationIntent(first, 'c1', message('a2', 'assistant'), () => `r${++count}`)
  )
  assert.equal(count, 1)
})

void test('action boundaries reject invalid feedback and only expose explicit share text', () => {
  assert.deepEqual(parseFeedback({ message_id: 'a', value: null }), {
    message_id: 'a',
    value: null
  })
  assert.throws(() => parseFeedback({ message_id: 'a', value: 'yes' }))
  assert.deepEqual(
    parseSharePreview({ message_id: 'a', question: null, answer: 'answer', content_hash: 'hash' }),
    { message_id: 'a', question: null, answer: 'answer', content_hash: 'hash' }
  )
  assert.throws(() => parseSharePreview({ question: 'q', answer: 'a' }))
  const shared = parseSharedMessage({
    question: 'q',
    answer: 'a',
    created_at: 'now',
    expires_at: 'later',
    reasoning: 'private',
    attachments: ['private']
  })
  assert.deepEqual(Object.keys(shared), ['question', 'answer', 'created_at', 'expires_at'])
  assert.throws(() =>
    parseMessageShare({
      id: 's',
      message_id: 'a',
      created_at: 'now',
      expires_at: 'later',
      revoked_at: 42
    })
  )
})
