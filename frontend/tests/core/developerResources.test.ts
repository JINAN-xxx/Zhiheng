import assert from 'node:assert/strict'
import test from 'node:test'
import * as api from '../../src/modules/developer/api/index.ts'
import * as docs from '../../src/modules/developer/apiDocs.ts'

const capabilities = {
  scopes: ['files:read', 'models:invoke'],
  definitions: [
    { scope: 'files:read', label: '文件', description: '读取', requires_models: false },
    { scope: 'models:invoke', label: '模型', description: '推理', requires_models: true }
  ],
  caps: { resource_rpm_min: 1, resource_rpm_max: 120 }
}
void test('catalog drives conditional model requirements and rejects empty or unknown scopes', () => {
  assert.equal(typeof api.parseScopeCatalog, 'function')
  const catalog = api.parseScopeCatalog(capabilities)
  assert.equal(api.keySelectionError(['files:read'], [], catalog), '')
  assert.match(api.keySelectionError(['models:invoke'], [], catalog), /模型/)
  assert.match(api.keySelectionError([], [], catalog), /能力/)
  assert.match(api.keySelectionError(['files:root'], [], catalog), /能力/)
  assert.throws(() => api.parseScopeCatalog({ ...capabilities, definitions: [] }))
})
void test('key and application decoders preserve resource fields, budgets, limits and dates', () => {
  assert.equal(typeof api.parseKey, 'function')
  const key = api.parseKey({
    id: 'key',
    name: 'Resource',
    application_id: 'app',
    prefix: 'sk-zh-masked',
    status: 'active',
    model_ids: [],
    scopes: ['files:read'],
    resource_rpm_limit: 7,
    rpm_limit: 2,
    tpm_limit: 100,
    concurrency_limit: 3,
    monthly_points_limit: '1.25',
    expires_at: 'tomorrow',
    last_used_at: null
  })
  assert.deepEqual(key.scopes, ['files:read'])
  assert.equal(key.resourceRpmLimit, 7)
  assert.equal(key.monthlyPointsLimit, '1.25')
  const app = api.parseApplication({
    id: 'app',
    owner_id: 'owner',
    code: 'old',
    name: 'Original',
    status: 'active',
    version: 2,
    created_at: 'created',
    updated_at: 'updated',
    source_file_limit: 1000,
    source_byte_limit: 1073741824
  })
  const changed = api.applyResourceVersion(app, 8, { sourceFileLimit: 10, sourceByteLimit: 20 })
  assert.deepEqual(changed, { ...app, version: 8, sourceFileLimit: 10, sourceByteLimit: 20 })
  assert.equal(app.version, 2)
  const refreshed = {
    ...app,
    applicationName: 'Renamed remotely',
    status: 'disabled' as const,
    updatedAt: 'new time'
  }
  const current = api.applyResourceVersion(app, 9, undefined, refreshed)
  assert.equal(current.applicationName, 'Renamed remotely')
  assert.equal(current.status, 'disabled')
  assert.equal(current.applicationCode, app.applicationCode)
})
void test('candidate actions remain the server delegated intersection and invalid bindings stay opaque', () => {
  assert.equal(typeof api.parseResourceCandidate, 'function')
  const shared = api.parseResourceCandidate({
    kind: 'knowledge_base',
    resource_id: 'kb',
    display_name: 'Shared',
    status: 'empty',
    owned: false,
    allowed_actions: ['read', 'query']
  })
  assert.deepEqual(shared.allowedActions, ['read', 'query'])
  const snapshot = api.parseResourceAccess({
    application_version: 4,
    source_count: 1,
    source_bytes: 5,
    source_file_limit: 1000,
    source_byte_limit: 1073741824,
    can_bind_resources: true,
    can_configure_quota: false,
    items: [
      {
        kind: 'file',
        resource_id: 'gone',
        display_name: '资源已失效',
        status: 'unavailable',
        owned: false,
        allowed_actions: [],
        actions: ['read'],
        available: false
      }
    ]
  })
  assert.equal(snapshot.items[0]?.displayName, '资源已失效')
  assert.equal(snapshot.sourceCount, 1)
  assert.equal(snapshot.canConfigureQuota, false)
})
void test('documentation renders every actual operation and resolves DTO fields and shared curl metadata', () => {
  assert.equal(typeof docs.parseOpenApiDocuments, 'function')
  const result = docs.parseOpenApiDocuments({
    openapi: '3.1.0',
    paths: {
      '/api/v1/skills': {
        get: {
          operationId: 'skills',
          summary: '技能列表',
          'x-required-scopes': ['skills:read'],
          responses: { '200': { description: 'OK' } },
          'x-codeSamples': [
            { lang: 'Shell', label: 'curl', source: 'curl {{ORIGIN}}/api/v1/skills' }
          ]
        }
      },
      '/api/v1/knowledge-bases': {
        post: {
          operationId: 'bases',
          summary: '创建知识库',
          requestBody: {
            content: { 'application/json': { schema: { $ref: '#/components/schemas/Create' } } }
          },
          responses: { '201': { description: 'Created' } },
          'x-codeSamples': [{ lang: 'Shell', label: 'curl', source: 'curl create knowledge' }]
        }
      }
    },
    components: {
      schemas: {
        Create: {
          type: 'object',
          required: ['name'],
          properties: { name: { type: 'string', maxLength: 100 } }
        }
      }
    }
  })
  assert.equal(result.length, 2)
  assert.equal(result[1]?.fields?.[0]?.name, 'name')
  assert.equal(result[1]?.fields?.[0]?.required, true)
  assert.equal(result[0]?.curlExample, 'curl {{ORIGIN}}/api/v1/skills')
})

void test('documentation requires one authoritative curl sample and never invents a fallback', () => {
  for (const samples of [
    undefined,
    [],
    [
      { lang: 'Shell', label: 'curl', source: 'curl basic' },
      { lang: 'Shell', label: 'curl · 可靠重试', source: 'curl retry' }
    ],
    [{ lang: 'Shell', label: 'curl', source: '' }],
    [{ lang: 'Python', label: 'python', source: 'print(1)' }]
  ]) {
    assert.throws(() =>
      docs.parseOpenApiDocuments({
        openapi: '3.1.0',
        paths: {
          '/api/v1/files': {
            post: {
              operationId: 'upload',
              responses: { '201': { description: 'Created' } },
              ...(samples === undefined ? {} : { 'x-codeSamples': samples })
            }
          }
        }
      })
    )
  }
})
