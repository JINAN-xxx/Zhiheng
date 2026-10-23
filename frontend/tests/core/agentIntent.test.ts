import assert from 'node:assert/strict'
import { test } from 'node:test'
import { nextPreviewIntent } from '../../src/modules/agents/api/index.ts'
import { hasUnpublishedAgentChanges } from '../../src/modules/agents/presentation.ts'
import type { Agent } from '../../src/modules/agents/api/index.ts'
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
    applicationId: 'app-1',
    temperature: 0.7,
    maxTokens: 512
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
})
