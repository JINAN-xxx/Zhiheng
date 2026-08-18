import assert from 'node:assert/strict'

import {
  buildApplicationPath,
  buildDeveloperKeyPayload,
  extractIssuedKey,
  normalizeApplicationPage,
  normalizeDeveloperKeys,
  shouldResetDeveloperKeys,
} from '../../plugins/ai/utils/application.js'

assert.deepEqual(normalizeApplicationPage({ data: { rows: [{ application_id: 'app-1', application_name: '内部工具', status: 'active' }], total: 1 } }), {
  rows: [{ applicationId: 'app-1', applicationCode: '', applicationName: '内部工具', status: 'active', version: 1, updatedAt: null }],
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
