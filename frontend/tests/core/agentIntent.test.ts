import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextPreviewIntent } from '../../src/modules/agents/api/index.ts'
import { hasUnpublishedAgentChanges } from '../../src/modules/agents/presentation.ts'
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
void test('publication sequence does not turn identical saved configuration into unpublished changes', () => {
  const config = {
    name: 'agent',
    description: '',
    modelId: 'model-1',
    systemPrompt: 'system',
    welcomeMessage: 'hello',
    skillSlugs: ['skill'],
    knowledgeBaseIds: [],
    knowledgeTopK: 8,
    applicationId: 'app-1',
    temperature: 0.7,
    maxTokens: 512,
    historyRounds: 20
  }
  const agent: Agent = {
    ...config,
    agentId: 'agent-1',
    ownerId: 'owner-1',
    version: 4,
    status: 'published',
    publishedVersion: 1,
    publishedConfig: { ...config, skillSlugs: [...config.skillSlugs] }
  }
  assert.equal(hasUnpublishedAgentChanges(agent), false)
  assert.equal(hasUnpublishedAgentChanges({ ...agent, systemPrompt: 'edited draft' }), true)
  assert.equal(agent.publishedConfig?.systemPrompt, 'system')
  assert.equal(hasUnpublishedAgentChanges({ ...agent, historyRounds: 0 }), true)
  assert.equal(hasUnpublishedAgentChanges({ ...agent, knowledgeTopK: 15 }), true)
})
void test('agent retrieval defaults to eight and keeps a cleared limit invalid', () => {
  const form = presentation.createAgentForm()
  assert.equal(form.knowledgeTopK, 8)
  form.knowledgeTopK = 15
  assert.equal(presentation.buildAgentPayload(form).knowledge_top_k, 15)
  form.knowledgeTopK = null
  assert.equal(Number.isNaN(presentation.buildAgentPayload(form).knowledge_top_k), true)
})
void test('agent history defaults to twenty, preserves zero, and keeps cleared input invalid', () => {
  const form = presentation.createAgentForm()
  assert.equal(form.historyRounds, 20)
  form.historyRounds = 0
  assert.equal(presentation.buildAgentPayload(form).history_rounds, 0)
  form.historyRounds = null
  assert.equal(Number.isNaN(presentation.buildAgentPayload(form).history_rounds), true)
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
  assert.deepEqual(presentation.selectPreviewMessages(display, 0), [
    { role: 'user', content: 'current' }
  ])
  assert.deepEqual(presentation.selectPreviewMessages(display, 1), [
    { role: 'user', content: 'new question' },
    { role: 'assistant', content: 'new answer' },
    { role: 'user', content: 'current' }
  ])
  assert.equal(presentation.selectPreviewMessages(display, 20).length, 5)
  assert.equal(JSON.stringify(display), original)
})
void test('lost-admission retry keeps its original history selection after draft rounds change', () => {
  const display = [
    { role: 'user' as const, content: 'previous' },
    { role: 'assistant' as const, content: 'answer', status: 'completed' },
    { role: 'user' as const, content: 'current' }
  ]
  const first = nextPreviewIntent(null, 'agent-1', display, () => 'first', 1)
  const retry = nextPreviewIntent(first, 'agent-1', display, () => 'second', 0)
  assert.equal(retry.clientRequestId, 'first')
  assert.equal(retry.messages.length, 3)
  assert.deepEqual(nextPreviewIntent(null, 'agent-1', display, () => 'next', 0).messages, [
    { role: 'user', content: 'current' }
  ])
})
