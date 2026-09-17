import assert from 'node:assert/strict'
import * as knowledge from '../../plugins/ai/utils/knowledge.js'

assert.equal(typeof knowledge.normalizeKnowledgeIndexResults, 'function', 'batch indexing must preserve successful files beside failures')
const parse = knowledge.normalizeKnowledgeIndexResults
const results = parse({ code: 200, data: { status: 'partial', items: [
  { id: 'doc-a', status: 'done' },
  { id: 'doc-b', status: 'failed', error: { code: 'parse_error', message: '解析失败' } },
] } }, 2)
assert.deepEqual(results, [{ documentId: 'doc-a', status: 'done', error: '' }, { documentId: 'doc-b', status: 'failed', error: '解析失败' }])
assert.deepEqual(parse({ code: 200, data: { status: 'failed', items: [{ id: 'doc-c', status: 'failed', error: '索引失败' }] } }, 1), [{ documentId: 'doc-c', status: 'failed', error: '索引失败' }])
assert.throws(() => parse({ code: 409, msg: '正在构建' }, 1), /正在构建/)
assert.throws(() => parse({ code: 200, data: { items: [{ status: 'done' }] } }, 2), /索引结果不完整/)
assert.throws(() => parse({ code: 200, data: { items: [{ status: 'pending' }] } }, 1), /索引结果无效/)
console.log('ok knowledge index results: mixed batch outcomes, structured failures and per-document retry IDs')
