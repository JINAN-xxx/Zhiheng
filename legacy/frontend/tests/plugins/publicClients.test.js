import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { loadPublicClients } from './helpers/loadPublicClients.js'

for (const name of ['agent_extension/public/skills', 'ai/public/resourceSelection', 'ai/public/applications', 'ai/public/developerKeys']) {
  assert.ok(existsSync(new URL(`../../plugins/${name}.js`, import.meta.url)), `${name} must exist`)
}
let pending
const requests = []
const clients = await loadPublicClients(config => { requests.push(config); return pending })
const { skills, resources, applications, keys } = clients
for (const [legacy, canonical, names] of [
  [clients.legacySkills, skills, ['listAvailableSkills']],
  [clients.legacyModels, resources, ['listPlatformModels']],
  [clients.legacyKnowledge, resources, ['listAvailableKnowledgeBases']],
  [clients.legacyApplications, applications, ['listApplications', 'createApplication']],
  [clients.legacyApplications, keys, ['listDeveloperKeys', 'createDeveloperKey', 'rotateDeveloperKey', 'revokeDeveloperKey']],
]) {
  for (const name of names) assert.equal(legacy[name], canonical[name], `${name} must have one implementation`)
}

const query = Object.freeze({ pageNum: 3, pageSize: 15, lifecycleStatus: 'active' })
const body = Object.freeze({ applicationName: 'Support', modelIds: ['model-a'], quota: { rpmLimit: 3 } })
const id = '应用/a.%'
const base = '/ai/applications/%E5%BA%94%E7%94%A8%2Fa%2E%25'
const cases = [
  [() => skills.listAvailableSkills(), { url: '/agent_extension/skills/available', method: 'get' }],
  [() => resources.listPlatformModels(query), { url: '/ai/models', method: 'get', params: query }],
  [() => resources.listAvailableKnowledgeBases(), { url: '/ai/knowledge/available', method: 'get' }],
  [() => applications.listApplications(query), { url: '/ai/applications', method: 'get', params: query }],
  [() => applications.createApplication(body), { url: '/ai/applications', method: 'post', data: body }],
  [() => keys.listDeveloperKeys(id), { url: `${base}/keys`, method: 'get' }],
  [() => keys.createDeveloperKey(id, body), { url: `${base}/keys`, method: 'post', data: body, headers: { repeatSubmit: false } }],
  [() => keys.rotateDeveloperKey(id, 'key.a', body), { url: `${base}/keys/key%2Ea/rotate`, method: 'post', data: body, headers: { repeatSubmit: false } }],
  [() => keys.revokeDeveloperKey(id, 'key.a'), { url: `${base}/keys/key%2Ea/revoke`, method: 'post' }],
]
for (const [call, config] of cases) {
  const value = { code: 200, data: { unchanged: true } }
  pending = Promise.resolve(value)
  const count = requests.length
  const result = call()
  assert.equal(result, pending)
  assert.equal(await result, value)
  assert.equal(requests.length, count + 1)
  assert.deepEqual(requests.at(-1), config)
  if (config.data) assert.equal(requests.at(-1).data, body)
  if (config.params) assert.equal(requests.at(-1).params, query)
  for (const error of [new Error('denied'), { code: 'FORBIDDEN' }, 'network failure']) {
    pending = Promise.reject(error)
    await assert.rejects(call(), caught => caught === error)
  }
}
for (const invalid of ['', '.', '..', 'bad\nvalue']) {
  const before = requests.length
  assert.throws(() => keys.listDeveloperKeys(invalid), /资源标识不合法/)
  assert.equal(requests.length, before)
}

assert.deepEqual(skills.normalizeAvailableSkills({ data: [{ skill_slug: 'research', skill_name: '研究', description: '资料', private: true }] }), [
  { skillSlug: 'research', skillName: '研究', description: '资料' },
])
assert.equal(skills.shouldOpenSkillPicker('hello @'), true)
assert.equal(skills.shouldOpenSkillPicker('user@example.com'), false)
assert.equal(skills.removeTerminalSkillTrigger('hello @'), 'hello ')
assert.equal(skills.removeTerminalSkillTrigger('user@example.com'), 'user@example.com')

assert.equal(resources.isCallablePlatformModel({ lifecycle_status: 'active', published_at: '2026-09-29' }), true)
assert.equal(resources.isCallablePlatformModel({ lifecycleStatus: 'active', publishedAt: null }), false)
assert.equal(resources.isCallablePlatformModel({ lifecycleStatus: 'disabled', publishedAt: '2026-09-29' }), false)
const pages = []
const selected = await resources.collectAllPages(async paging => {
  pages.push(paging)
  return resources.normalizeApplicationModelPage({ data: { rows: paging.pageNum === 1 ? [{ modelId: 'a' }, { modelId: 'b' }] : [{ modelId: 'c' }], total: 3 } })
}, { pageSize: 2 })
assert.deepEqual(selected.map(row => row.modelId), ['a', 'b', 'c'])
assert.deepEqual(pages, [{ pageNum: 1, pageSize: 2 }, { pageNum: 2, pageSize: 2 }])
const failure = new Error('page unavailable')
await assert.rejects(resources.collectAllPages(async () => { throw failure }), error => error === failure)
assert.equal(resources.normalizeKnowledgeDatabases({ code: 200, data: { databases: [{ db_id: 'kb-a', database_name: '资料库' }] } })[0].name, '资料库')
assert.throws(() => resources.normalizeKnowledgeDatabases({ code: 403, msg: 'forbidden' }), /forbidden/)
assert.equal(applications.normalizeApplication({ application_id: 'app-a', application_name: '应用' }).applicationId, 'app-a')
assert.equal(applications.normalizeApplicationPage({ data: { rows: [{ application_id: 'app-a' }], total: 31 } }).total, 31)

const issued = { key_id: 'key-a', key_name: 'new', api_key: 'fixture-secret', fingerprint: 'fp' }
assert.equal(keys.extractIssuedKey({ data: issued }).apiKey, 'fixture-secret')
assert.equal(keys.normalizeDeveloperKeys({ data: [issued] })[0].apiKey, undefined, 'List/display normalization must not expose an issued secret')
assert.throws(() => keys.extractIssuedKey({ data: { key_id: 'key-a', fingerprint: 'fp' } }), /访问密钥响应无效/)
assert.equal(keys.developerKeyStatusLabel('revoked'), '已撤销')
assert.equal(keys.buildPublicApiBase('https://example.test/', '/docker-api/'), 'https://example.test/docker-api/v1')
let copied
assert.equal(await keys.copyText('endpoint', { clipboard: { writeText: async value => { copied = value } }, documentObject: null }), true)
assert.equal(copied, 'endpoint')
assert.equal(await keys.copyText('endpoint', { clipboard: null, documentObject: null }), false)

// Guard all static cross-plugin imports in the two business plugins, not imports within their own module.
for (const [owner, other] of [['ai', 'agent_extension'], ['agent_extension', 'ai']]) {
  const root = new URL(`../../plugins/${owner}/`, import.meta.url)
  for (const file of readdirSync(root, { recursive: true })) {
    if (!/\.(vue|js)$/.test(file)) continue
    const source = readFileSync(new URL(file, root), 'utf8')
    for (const [, specifier] of source.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)) {
      if (!specifier.startsWith('.')) continue
      const path = new URL(specifier, new URL(file, root)).pathname
      const marker = `/plugins/${other}/`
      if (path.includes(marker)) assert.ok(path.split(marker)[1].startsWith('public/'), `${owner}/${file} imports ${specifier}`)
    }
  }
}
console.log('ok four public clients: compatibility, request contracts, options, pagination, key display and import boundaries')
