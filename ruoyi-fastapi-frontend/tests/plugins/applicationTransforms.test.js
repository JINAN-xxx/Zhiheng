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
