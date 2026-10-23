import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../../plugins/ai/api/modelEngine.js', import.meta.url), 'utf8')
const requests = []
const request = (config) => { requests.push(config); return Promise.resolve({ data: { success: true } }) }
const api = new Function('request', 'buildSafeResourcePath', 'buildSecretBodyRequest',
  source.replace(/^import .+$/gm, '').replace(/export /g, '')
  + '\nreturn { testEngineConnection, listEngineProviders };')(
  request,
  (base, id) => `${base}/${id}`,
  (data) => ({ data, headers: { repeatSubmit: false } }),
)
await api.testEngineConnection({ upstreamModel: 'Qwen/Qwen3.8-27B', values: { api_key: 'test-only' } })
assert.equal(requests[0].timeout, 120000, 'model probe must outlive the gateway 60-second deadline')
assert.equal(requests[0].headers.repeatSubmit, false, 'credential body must not enter the repeat-submit cache')
await api.listEngineProviders()
assert.equal(requests[1].timeout, undefined, 'ordinary management requests keep the normal timeout')
