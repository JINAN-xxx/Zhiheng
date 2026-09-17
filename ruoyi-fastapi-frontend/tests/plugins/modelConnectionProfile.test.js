import assert from 'node:assert/strict'
import { normalizeCredential, normalizeProvider, curateModelProviders, isCredentialCompatible } from '../../plugins/ai/utils/modelEngine.js'

const providers = curateModelProviders([
  { provider: 'OpenAI_Compatible', displayName: 'Compatible', litellmProvider: 'openai' },
  { provider: 'OpenAI', litellmProvider: 'openai' },
  { provider: 'Deepseek', litellmProvider: 'deepseek' },
  { provider: 'CUSTOM_OPENAI', litellmProvider: 'custom_openai' },
].map(normalizeProvider))
const silicon = normalizeCredential({ credentialName: 'old-embedding', provider: 'openai',
  connectionProfile: { status: 'ready', apiBase: 'https://api.siliconflow.cn/v1', providerIds: ['OpenAI_Compatible', 'CUSTOM_OPENAI'] } })
const deepseek = normalizeCredential({ credentialName: 'dp', provider: 'openai',
  connectionProfile: { status: 'ready', apiBase: 'https://api.deepseek.com', providerIds: ['Deepseek', 'CUSTOM_OPENAI'] } })
assert.equal(isCredentialCompatible(silicon, 'OpenAI_Compatible', providers, { requireProfile: true }), true)
assert.equal(isCredentialCompatible(silicon, 'OpenAI', providers, { requireProfile: true }), false)
assert.equal(isCredentialCompatible(deepseek, 'OpenAI_Compatible', providers, { requireProfile: true }), false)
assert.equal(isCredentialCompatible(deepseek, 'Deepseek', providers, { requireProfile: true }), true)
assert.equal(silicon.connectionProfile.apiBase, 'https://api.siliconflow.cn/v1')

for (const status of ['missing', 'conflict', 'invalid']) {
  const value = normalizeCredential({ credentialName: status, provider: 'openai', connectionProfile: {
    status, apiBase: null, providerIds: ['OpenAI_Compatible'],
  } })
  assert.equal(isCredentialCompatible(value, 'OpenAI_Compatible', providers, { requireProfile: true }), false)
}
assert.equal(isCredentialCompatible({ name: 'unknown', provider: 'openai' }, 'OpenAI_Compatible', providers, { requireProfile: true }), false)
for (const apiBase of ['https://user:secret@example.test/v1', 'https://example.test/v1?api_key=secret', 'https://example.test/v1#secret', 'javascript:secret']) {
  const value = normalizeCredential({ credentialName: 'unsafe', provider: 'openai', connectionProfile: {
    status: 'ready', apiBase, providerIds: ['OpenAI_Compatible'], apiKey: 'never-serialize',
  } })
  assert.equal(isCredentialCompatible(value, 'OpenAI_Compatible', providers, { requireProfile: true }), false)
  assert.doesNotMatch(JSON.stringify(value), /secret|never-serialize/)
}
