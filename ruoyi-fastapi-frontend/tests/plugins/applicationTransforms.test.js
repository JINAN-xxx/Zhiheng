import assert from 'node:assert/strict'

import {
  aggregateGrantedModels,
  applicationTabResources,
  buildApplicationPath,
  buildDeveloperKeyPayload,
  canViewApplicationModels,
  extractIssuedKey,
  normalizeApplicationTab,
  normalizeApplicationPage,
  normalizeApplicationModelPage,
  normalizeDeveloperKeys,
  isCallablePlatformModel,
  resolvePreferredApplication,
  shouldResetDeveloperKeys,
} from '../../plugins/ai/utils/application.js'
import { apiDocNavigation, apiDocs, buildPublicApiBase, chatStreamFrames, publicApiErrors } from '../../plugins/ai/utils/apiDocs.js'

assert.equal(buildPublicApiBase('http://127.0.0.1:12580/', '/docker-api/'), 'http://127.0.0.1:12580/docker-api/v1')
assert.equal(buildPublicApiBase('http://127.0.0.1:12580', 'docker-api'), 'http://127.0.0.1:12580/docker-api/v1')
assert.equal(buildPublicApiBase('http://127.0.0.1:12580/', ''), 'http://127.0.0.1:12580/v1')
assert.deepEqual(apiDocs.map((item) => `${item.method} ${item.path}`), [
  'GET /v1/models',
  'POST /v1/chat/completions',
  'POST /v1/embeddings',
  'POST /v1/rerank',
])
assert.equal(apiDocs.find((item) => item.id === 'chat-completions').streaming, true)
assert.ok(apiDocs.every((item) => !JSON.stringify(item).match(/sk-(?:zh|live|test)|sk-[A-Za-z0-9]{10,}/)))
assert.ok(apiDocs.every((item) => typeof item.curlExample === 'string' && typeof item.pythonExample === 'string'))

const chatDocs = apiDocs.find((item) => item.id === 'chat-completions')
assert.deepEqual(chatDocs.fields.filter((field) => ['presence_penalty', 'frequency_penalty'].includes(field.name)).map((field) => field.name), ['presence_penalty', 'frequency_penalty'])
assert.deepEqual(chatDocs.normalResponseFields.map((field) => field.name), [
  'id', 'object', 'created', 'model', 'choices[].message', 'choices[].finish_reason', 'usage',
])
assert.deepEqual(chatDocs.streamResponseFields.map((field) => field.name), [
  'id', 'object', 'created', 'model', 'choices[].delta', 'choices[].finish_reason',
])
assert.match(chatDocs.normalRequestExample, /"stream": false/)
assert.match(chatDocs.streamRequestExample, /"stream": true/)
assert.equal(chatStreamFrames.length, 4)
for (const frame of chatStreamFrames.slice(0, 3)) {
  assert.equal(frame.object, 'chat.completion.chunk')
  assert.equal(typeof frame.created, 'number')
  assert.equal(frame.model, '<MODEL_CODE>')
  assert.equal(frame.choices.length, 1)
}
assert.deepEqual(chatStreamFrames[0].choices[0], { index: 0, delta: { role: 'assistant' }, finish_reason: null })
assert.deepEqual(chatStreamFrames[1].choices[0], { index: 0, delta: { content: '你好' }, finish_reason: null })
assert.deepEqual(chatStreamFrames[2].choices[0], { index: 0, delta: {}, finish_reason: 'stop' })
assert.equal(chatStreamFrames[3], '[DONE]')
assert.deepEqual(publicApiErrors.map((item) => item.code), [
  'not_found', 'invalid_api_key', 'permission_denied', 'model_not_found', 'invalid_request', 'upstream_error',
  'service_unavailable', 'request_too_large', 'rate_limit_exceeded', 'concurrency_limit_exceeded', 'budget_exceeded',
])
assert.deepEqual(publicApiErrors.map(({ status, type }) => [status, type]), [
  [404, 'invalid_request_error'], [401, 'authentication_error'], [403, 'permission_error'], [404, 'invalid_request_error'],
  [400, 'invalid_request_error'], [502, 'api_error'], [503, 'api_error'], [413, 'invalid_request_error'],
  [429, 'rate_limit_error'], [429, 'rate_limit_error'], [429, 'rate_limit_error'],
])
assert.deepEqual(apiDocNavigation.map((item) => item.anchor), [
  'api-overview', 'api-auth', 'api-models', 'api-chat-completions', 'api-embeddings', 'api-rerank', 'api-streaming', 'api-errors',
])

assert.deepEqual(normalizeApplicationPage({ data: { rows: [{ application_id: 'app-1', application_name: '内部工具', status: 'active' }], total: 1 } }), {
  rows: [{ applicationId: 'app-1', applicationCode: '', applicationName: '内部工具', status: 'active', version: 1, createdAt: null, updatedAt: null }],
  total: 1,
})

const keys = normalizeDeveloperKeys({ data: [{ keyId: 'key-1', keyName: '服务端', maskedKey: 'sk-***', apiKey: 'must-not-survive', status: 'active', grants: [] }] })
assert.equal(keys[0].apiKey, undefined)
assert.equal(keys[0].maskedKey, 'sk-***')

assert.deepEqual(extractIssuedKey({ data: { keyId: 'key-2', keyName: 'CI', apiKey: 'sk-live-once', fingerprint: 'abcd' } }), {
  keyId: 'key-2',
  keyName: 'CI',
  apiKey: 'sk-live-once',
  fingerprint: 'abcd',
})

assert.equal(buildApplicationPath('app_123', 'keys/key_456/rotate'), '/ai/applications/app_123/keys/key_456/rotate')
assert.throws(() => buildApplicationPath('..', 'keys'), /资源标识不合法/)

assert.deepEqual(buildDeveloperKeyPayload({
  keyName: '生产服务',
  modelIds: ['mdl_1'],
  expiresAt: new Date('2026-08-18T08:00:00Z'),
  rpmLimit: 120,
  tpmLimit: null,
  concurrencyLimit: 8,
}), {
  keyName: '生产服务',
  modelIds: ['mdl_1'],
  expiresAt: '2026-08-18T08:00:00.000Z',
  quota: { warningRatio: '0.8', hardLimitRatio: '1', currency: 'USD', rpmLimit: 120, concurrencyLimit: 8 },
})

assert.equal(shouldResetDeveloperKeys('app_1', 'app_1'), false)
assert.equal(shouldResetDeveloperKeys('app_1', 'app_2'), true)
assert.equal(shouldResetDeveloperKeys('app_1', null), true)

assert.equal(normalizeApplicationTab('keys'), 'keys')
assert.equal(normalizeApplicationTab('unexpected'), 'overview')
assert.equal(normalizeApplicationTab(['usage']), 'usage')

assert.deepEqual(aggregateGrantedModels([
  { keyId: 'key-1', keyName: '生产服务', status: 'active', grants: [{ modelId: 'mdl-1', status: 'active' }, { modelId: 'mdl-2', status: 'active' }] },
  { keyId: 'key-2', keyName: '定时任务', status: 'revoked', grants: [{ modelId: 'mdl-1', status: 'active' }] },
  { keyId: 'key-3', keyName: '数据服务', status: 'active', grants: [{ modelId: 'mdl-1', status: 'disabled' }] },
], [
  { modelId: 'mdl-1', modelName: '通用对话', modelCode: 'chat-main', lifecycleStatus: 'active', publishedAt: '2026-08-01T08:00:00Z' },
  { model_id: 'mdl-2', display_name: '文本向量', model_code: 'embedding-main', lifecycle_status: 'active', published_at: '2026-08-02T08:00:00Z' },
]), [
  { modelId: 'mdl-1', modelName: '通用对话', modelCode: 'chat-main', available: true, keyCount: 2, activeKeyCount: 1 },
  { modelId: 'mdl-2', modelName: '文本向量', modelCode: 'embedding-main', available: true, keyCount: 1, activeKeyCount: 1 },
])

assert.equal(isCallablePlatformModel({ lifecycleStatus: 'active', publishedAt: '2026-08-01T08:00:00Z' }), true)
assert.equal(isCallablePlatformModel({ lifecycle_status: 'active', published_at: '2026-08-01T08:00:00Z' }), true)
assert.equal(isCallablePlatformModel({ lifecycleStatus: 'active', publishedAt: null }), false)
assert.equal(isCallablePlatformModel({ lifecycleStatus: 'disabled', publishedAt: '2026-08-01T08:00:00Z' }), false)

assert.deepEqual(aggregateGrantedModels([
  { keyId: 'key-1', status: 'active', grants: [{ modelId: 'mdl-disabled', status: 'active' }, { modelId: 'mdl-missing', status: 'active' }] },
], [
  { modelId: 'mdl-disabled', displayName: '已下线模型', modelCode: 'disabled-model', lifecycleStatus: 'disabled', publishedAt: '2026-08-01T08:00:00Z' },
]), [
  { modelId: 'mdl-disabled', modelName: '已下线模型', modelCode: 'disabled-model', available: false, keyCount: 1, activeKeyCount: 0 },
  { modelId: 'mdl-missing', modelName: '模型信息不可用', modelCode: 'mdl-missing', available: false, keyCount: 1, activeKeyCount: 0 },
])

assert.deepEqual(applicationTabResources('overview'), [])
assert.deepEqual(applicationTabResources('keys'), ['keys'])
assert.deepEqual(applicationTabResources('models'), ['keys', 'models'])
assert.deepEqual(applicationTabResources('usage'), ['usage'])
assert.equal(canViewApplicationModels(true, true), true)
assert.equal(canViewApplicationModels(true, false), false)
assert.equal(canViewApplicationModels(false, true), false)
assert.equal(canViewApplicationModels(false, false), false)
assert.deepEqual(normalizeApplicationModelPage({ data: { rows: [{ modelId: 'mdl-1' }], total: 1 } }), {
  rows: [{ modelId: 'mdl-1' }],
  total: 1,
})

let requestedApplicationId = null
const deepLinkedApplication = await resolvePreferredApplication(
  [{ applicationId: 'app-1', applicationName: '当前页应用' }],
  'app-42',
  async (applicationId) => {
    requestedApplicationId = applicationId
    return { data: { applicationId, applicationName: '分页外应用', status: 'active' } }
  },
)
assert.equal(requestedApplicationId, 'app-42')
assert.equal(deepLinkedApplication.applicationId, 'app-42')
assert.equal(deepLinkedApplication.applicationName, '分页外应用')

let pageHitRequestedDetail = false
const pageApplication = await resolvePreferredApplication(
  [{ applicationId: 'app-1', applicationName: '当前页应用' }],
  'app-1',
  async () => { pageHitRequestedDetail = true },
)
assert.equal(pageApplication.applicationId, 'app-1')
assert.equal(pageHitRequestedDetail, false)

let invalidIdRequestedDetail = false
const invalidIdFallback = await resolvePreferredApplication(
  [{ applicationId: 'app-1', applicationName: '当前页应用' }],
  '..',
  async () => { invalidIdRequestedDetail = true },
)
assert.equal(invalidIdFallback.applicationId, 'app-1')
assert.equal(invalidIdRequestedDetail, false)

const fallbackApplication = await resolvePreferredApplication(
  [{ applicationId: 'app-1', applicationName: '当前页应用' }],
  'app-missing',
  async () => { throw { response: { status: 404 } } },
)
assert.equal(fallbackApplication.applicationId, 'app-1')

const invalidDetailFallback = await resolvePreferredApplication(
  [{ applicationId: 'app-1', applicationName: '当前页应用' }],
  'app-invalid',
  async () => ({ data: {} }),
)
assert.equal(invalidDetailFallback.applicationId, 'app-1')

const acceptedNotFoundFallback = await resolvePreferredApplication(
  [{ applicationId: 'app-1', applicationName: '当前页应用' }],
  'app-not-found',
  async () => null,
)
assert.equal(acceptedNotFoundFallback.applicationId, 'app-1')

await assert.rejects(
  resolvePreferredApplication(
    [{ applicationId: 'app-1', applicationName: '当前页应用' }],
    'app-error',
    async () => { throw { response: { status: 500 } } },
  ),
)
