import assert from 'node:assert/strict'
import { test } from 'node:test'

void test('workspace options use its authorized endpoint without exposing connection credentials', async () => {
  const previous = globalThis.fetch
  globalThis.fetch = (input) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    assert.equal(url, '/api/work/options')
    return Promise.resolve(
      new Response(
        JSON.stringify({
          models: [{ id: 'm1', name: '工作模型', max_output_tokens: 8192 }],
          skills: [{ slug: 'documents', name: '文档处理', description: '整理资料', version: 1 }],
          can_execute_code: true,
          sandbox_available: true
        })
      )
    )
  }
  try {
    const { getWorkOptions } = await import('../../src/modules/work/api/index.ts')
    const result = await getWorkOptions(new AbortController().signal)
    assert.deepEqual(result.models, [{ id: 'm1', name: '工作模型', maxOutputTokens: 8192 }])
    assert.equal(result.skills[0]?.slug, 'documents')
    assert.equal(result.canExecuteCode, true)
    assert.equal(result.sandboxAvailable, true)
    assert.equal('connection_id' in result.models[0]!, false)
  } finally {
    globalThis.fetch = previous
  }
})
