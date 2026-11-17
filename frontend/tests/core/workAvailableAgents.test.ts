import assert from 'node:assert/strict'
import { test } from 'node:test'

void test('Work accepts available assistants without a publication or configuration revision', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = (input) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    assert.equal(url, '/api/work/agents/available')
    return Promise.resolve(
      new Response(JSON.stringify([{ id: 'agent-1', name: '最新配置助手', description: '用途' }]))
    )
  }
  try {
    const { availableAgents } = await import('../../src/modules/work/api/index.ts')
    assert.deepEqual(await availableAgents(null, new AbortController().signal), [
      { agentId: 'agent-1', name: '最新配置助手', description: '用途' }
    ])
  } finally {
    globalThis.fetch = previous
  }
})
