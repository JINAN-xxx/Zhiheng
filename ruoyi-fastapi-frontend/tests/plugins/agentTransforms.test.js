import assert from 'node:assert/strict'
import test from 'node:test'
import {
  agentStatusLabel,
  buildAgentPayload,
  buildAgentPreviewPayload,
  buildAgentPublicPath,
  createAgentForm,
  extractAgentCompletion,
  getPublishedAgentAccess,
  hasUnpublishedAgentChanges,
  normalizeAgent,
  normalizeAgentPage,
} from '../../plugins/agent_extension/utils/agent.js'

test('normalizes an agent page and preserves zero-valued temperature', () => {
  const page = normalizeAgentPage({ data: { rows: [{ agentId: 'agent-1', name: '助手', temperature: 0, version: 3 }], total: 20 } })
  assert.equal(page.total, 20)
  assert.equal(page.rows[0].agentId, 'agent-1')
  assert.equal(page.rows[0].temperature, 0)
  assert.equal(page.rows[0].version, 3)
  assert.deepEqual(page.rows[0].knowledgeBaseIds, [])
  assert.deepEqual(normalizeAgentPage({ rows: [], total: 0 }), { rows: [], total: 0 })
})

test('creates independent form arrays and sends only editable fields with the expected version', () => {
  const source = normalizeAgent({ agentId: 'agent-1', name: ' 助手 ', modelId: 'model-1', skillSlugs: ['report'], knowledgeBaseIds: ['kb-1'], version: 4 })
  const form = createAgentForm(source)
  form.skillSlugs.push('summary')
  form.knowledgeBaseIds.push(' kb-1 ', ' kb-2 ', '')
  assert.deepEqual(source.skillSlugs, ['report'])
  assert.deepEqual(buildAgentPayload(form, source.version), {
    name: '助手', description: '', modelId: 'model-1', systemPrompt: '', welcomeMessage: '', skillSlugs: ['report', 'summary'],
    knowledgeBaseIds: ['kb-1', 'kb-2'], applicationId: null, temperature: 0.7, maxTokens: 2048, expectedVersion: 4,
  })
  assert.equal(Object.hasOwn(buildAgentPayload(form), 'expectedVersion'), false)
})

test('distinguishes draft edits from the immutable published version', () => {
  assert.equal(hasUnpublishedAgentChanges({ status: 'draft', version: 1, publishedVersion: null }), false)
  assert.equal(hasUnpublishedAgentChanges({ status: 'published', version: 3, publishedVersion: 2 }), true)
  assert.equal(hasUnpublishedAgentChanges({ status: 'published', version: 2, publishedVersion: 2 }), false)
  assert.equal(agentStatusLabel({ status: 'published', version: 3, publishedVersion: 2 }), '已发布 · 有未发布更改')
})

test('welcome text survives editing and supports older agents and snapshots', () => {
  const welcome = '你好，我是文档助手。\n可以把问题发给我。'
  assert.equal(createAgentForm().welcomeMessage, '')
  assert.equal(createAgentForm({ welcome_message: welcome }).welcomeMessage, welcome)
  assert.equal(buildAgentPayload({ name: '助手', welcomeMessage: welcome }).welcomeMessage, welcome)
  const agent = normalizeAgent({
    welcomeMessage: '新的草稿欢迎语', publishedConfig: { welcomeMessage: welcome },
  })
  assert.equal(agent.welcomeMessage, '新的草稿欢迎语')
  assert.equal(agent.publishedConfig.welcomeMessage, welcome)
  assert.equal(normalizeAgent({ publishedConfig: {} }).publishedConfig.welcomeMessage, '')
})

test('uses valid numeric defaults after a sampling input is cleared', () => {
  const payload = buildAgentPayload({ temperature: null, maxTokens: null })
  assert.equal(payload.temperature, 0.7)
  assert.equal(payload.maxTokens, 2048)
})

test('key access resolves the published model and application instead of a changed draft', () => {
  const agent = normalizeAgent({
    agentId: 'agent-1', status: 'published', version: 4, publishedVersion: 3,
    modelId: 'draft-model', applicationId: 'draft-app',
    publishedConfig: { modelId: 'published-model', applicationId: 'published-app' },
  })
  assert.deepEqual(getPublishedAgentAccess(agent), { modelId: 'published-model', applicationId: 'published-app' })
  assert.equal(getPublishedAgentAccess({ ...agent, status: 'draft' }), null)
  assert.equal(getPublishedAgentAccess({ ...agent, publishedConfig: null }), null)
})

test('preview accepts only conversation text and never sends configured overrides', () => {
  assert.deepEqual(buildAgentPreviewPayload([
    { role: 'user', content: '你好', localId: 'local-1' },
    { role: 'assistant', content: '您好' },
  ]), { messages: [{ role: 'user', content: '你好' }, { role: 'assistant', content: '您好' }], stream: false })
  assert.throws(() => buildAgentPreviewPayload([{ role: 'system', content: 'override' }]), /消息/)
  assert.throws(() => buildAgentPreviewPayload([{ role: 'user', content: '' }]), /消息/)
  assert.throws(() => buildAgentPreviewPayload([]), /消息/)
})

test('extracts a wrapped completion and rejects malformed successful responses', () => {
  assert.equal(extractAgentCompletion({ data: { choices: [{ message: { role: 'assistant', content: '答复' } }] } }), '答复')
  assert.throws(() => extractAgentCompletion({ data: {} }), /响应/)
})

test('encodes agent path segments and rejects path traversal identifiers', () => {
  assert.equal(buildAgentPublicPath('uuid-1'), '/v1/agents/uuid-1/chat/completions')
  assert.equal(buildAgentPublicPath('a/b'), '/v1/agents/a%2Fb/chat/completions')
  assert.throws(() => buildAgentPublicPath('..'), /标识/)
})
