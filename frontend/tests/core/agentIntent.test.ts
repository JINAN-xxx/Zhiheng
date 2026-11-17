import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextPreviewIntent } from '../../src/modules/agents/api/index.ts'
import { agentStatusLabel, getAgentAccess } from '../../src/modules/agents/presentation.ts'
import type { Agent } from '../../src/modules/agents/api/index.ts'
import * as presentation from '../../src/modules/agents/presentation.ts'
void test('preview retry retains its key and copied history after a lost admission response', () => {
  let ids = 0
  const messages = [{ role: 'user' as const, content: 'question' }]
  const first = nextPreviewIntent(null, 'agent-1', messages, () => `id-${++ids}`)
  messages[0]!.content = 'edited'
  assert.equal(first.messages[0]?.content, 'question')
  const retry = nextPreviewIntent(
    first,
    'agent-1',
    [{ role: 'user', content: 'question' }],
    () => `id-${++ids}`
  )
  assert.equal(retry.clientRequestId, first.clientRequestId)
  assert.notEqual(
    nextPreviewIntent(first, 'agent-1', messages, () => `id-${++ids}`).clientRequestId,
    first.clientRequestId
  )
  assert.notEqual(
    nextPreviewIntent(first, 'agent-2', first.messages, () => `id-${++ids}`).clientRequestId,
    first.clientRequestId
  )
})
void test('assistant has one latest configuration and activation controls API access', () => {
  const form = presentation.createAgentForm()
  const agent: Agent = {
    ...form,
    name: 'agent',
    modelId: 'model-1',
    knowledgeTopK: 8,
    applicationId: 'app-1',
    temperature: 0.7,
    maxTokens: 512,
    agentId: 'agent-1',
    ownerId: 'owner-1',
    version: 4,
    status: 'enabled'
  }
  assert.equal(agentStatusLabel(agent), '已启用')
  assert.deepEqual(getAgentAccess(agent), { applicationId: 'app-1', modelId: 'model-1' })
  assert.equal(getAgentAccess({ ...agent, status: 'disabled' }), null)
  assert.equal('history_rounds' in presentation.buildAgentPayload(agent), false)
  assert.equal('publishedConfig' in agent, false)
})
void test('agent retrieval defaults to eight and keeps a cleared limit invalid', () => {
  const form = presentation.createAgentForm()
  assert.equal(form.knowledgeTopK, 8)
  form.knowledgeTopK = 15
  assert.equal(presentation.buildAgentPayload(form).knowledge_top_k, 15)
  form.knowledgeTopK = null
  assert.equal(Number.isNaN(presentation.buildAgentPayload(form).knowledge_top_k), true)
})
void test('preview selects complete recent rounds without mutating displayed history', () => {
  assert.equal(typeof presentation.selectPreviewMessages, 'function')
  const display = [
    { role: 'user' as const, content: 'old question' },
    { role: 'assistant' as const, content: 'old answer', status: 'completed' },
    { role: 'user' as const, content: 'failed question' },
    { role: 'assistant' as const, content: 'partial', status: 'failed' },
    { role: 'user' as const, content: 'stopped question' },
    { role: 'assistant' as const, content: 'partial', status: 'cancelled' },
    { role: 'user' as const, content: 'new question' },
    { role: 'assistant' as const, content: 'new answer', status: 'completed' },
    { role: 'user' as const, content: 'current' }
  ]
  const original = JSON.stringify(display)
  assert.deepEqual(presentation.selectPreviewMessages(display), [
    { role: 'user', content: 'old question' },
    { role: 'assistant', content: 'old answer' },
    { role: 'user', content: 'new question' },
    { role: 'assistant', content: 'new answer' },
    { role: 'user', content: 'current' }
  ])
  assert.equal(JSON.stringify(display), original)
})
void test('lost-admission retry keeps its copied history after latest configuration changes', () => {
  const display = [
    { role: 'user' as const, content: 'previous' },
    { role: 'assistant' as const, content: 'answer', status: 'completed' },
    { role: 'user' as const, content: 'current' }
  ]
  const first = nextPreviewIntent(null, 'agent-1', display, () => 'first')
  const retry = nextPreviewIntent(first, 'agent-1', display, () => 'second')
  assert.equal(retry.clientRequestId, 'first')
  assert.equal(retry.messages.length, 3)
  assert.deepEqual(
    nextPreviewIntent(null, 'agent-1', display, () => 'next').messages,
    first.messages
  )
})
