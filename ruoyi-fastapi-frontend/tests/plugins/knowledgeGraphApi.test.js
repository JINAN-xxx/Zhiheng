import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { buildKnowledgeGraphParams } from '../../plugins/ai/utils/knowledge.js'

const source = readFileSync(new URL('../../plugins/ai/api/knowledge.js', import.meta.url), 'utf8')
const requests = []
const scope = { request: config => { requests.push(config); return Promise.resolve({ code: 200 }) }, buildKnowledgeGraphParams }
vm.createContext(scope)
vm.runInContext(source.replace(/^import .+\n/gm, '').replaceAll('export const ', 'var '), scope)
assert.equal(typeof scope.getKnowledgeGraphConfig, 'function', 'graph settings need a protected configuration endpoint')
const signal = new AbortController().signal
const config = { llm_model_id: 'model', reasoning_strategy: 'auto', timeout_seconds: 240, max_output_tokens: 16384, concurrency: 2 }
await scope.getKnowledgeGraphConfig('kb/a', signal)
await scope.saveKnowledgeGraphConfig('kb/a', config, signal)
await scope.getKnowledgeGraphPreview('kb/a', signal)
await scope.startKnowledgeGraphPreview('kb/a', { config, document_id: 'doc' }, signal)
assert.deepEqual(requests.map(item => item.method), ['get', 'put', 'get', 'post'])
assert.deepEqual(requests.map(item => item.url), ['/ai/knowledge/databases/kb%2Fa/graph-config', '/ai/knowledge/databases/kb%2Fa/graph-config', '/ai/knowledge/databases/kb%2Fa/graph-preview', '/ai/knowledge/databases/kb%2Fa/graph-preview'])
assert.ok(requests.every(item => item.signal === signal), 'all graph requests must be cancelable on navigation')
assert.equal(requests[1].data, config)
assert.equal(requests[3].headers.repeatSubmit, false)
await scope.listKnowledgeGraphLabels('kb/a', signal)
await scope.getKnowledgeSubgraph('kb/a', { node_label: '*', max_depth: 2, max_nodes: 100 }, signal)
assert.equal(requests[4].signal, signal, 'label reads must be cancelable when the active graph version changes')
assert.equal(requests[5].signal, signal, 'subgraph reads must be cancelable when the active graph version changes')
console.log('ok graph API: protected endpoints, complete draft settings and cancellation')
