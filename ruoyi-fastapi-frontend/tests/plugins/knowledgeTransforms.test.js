import assert from 'node:assert/strict'
import {
  unwrapKnowledgeResponse, normalizeKnowledgeDatabases, normalizeKnowledgeTypes,
  buildKnowledgeDatabasePayload, validateChunkParams, normalizeKnowledgeGraph,
  buildKnowledgeGraphParams, resetKnowledgeSelection, knowledgeFolderTrail,
  filterKnowledgeDocuments, normalizeKnowledgeQuery, buildKnowledgeLocation,
  normalizeKnowledgeView,
} from '../../plugins/ai/utils/knowledge.js'

assert.throws(() => unwrapKnowledgeResponse({ code: 200, data: { status: 'failed', message: '模型不可用' } }), /模型不可用/)
assert.throws(() => unwrapKnowledgeResponse({ code: 200, data: { status: 'success', items: [{ status: 'done' }, { status: 'failed', error: '解析失败' }] } }), /解析失败/)
assert.deepEqual(unwrapKnowledgeResponse({ code: 200, data: { databases: [] } }), { databases: [] })
assert.throws(() => unwrapKnowledgeResponse({ code: 503, msg: '服务未配置' }), /服务未配置/)

assert.deepEqual(normalizeKnowledgeDatabases({ code: 200, data: { databases: [{ db_id: 'kb-1', name: '<img onerror=alert(1)>', enable_knowledge_graph: false }] } }).map(row => row.name), ['<img onerror=alert(1)>'])
const types = normalizeKnowledgeTypes({ kb_types: { milvus: { available: false }, chroma: { available: true }, lightrag: { available: false } } })
assert.deepEqual(types.map(type => type.value), ['milvus'])
assert.equal(types.find(type => type.available), undefined)
assert.equal(types.find(type => type.value === 'milvus').disabled, true)
assert.throws(() => buildKnowledgeDatabasePayload({ database_name: '库', embed_model_id: '' }), /Embedding/)
assert.throws(() => buildKnowledgeDatabasePayload({ database_name: '库', embed_model_id: 'embed', enable_knowledge_graph: true }), /对话模型/)
for (const kb_type of [undefined, '', 'milvus', 'chroma', 'lightrag']) {
  assert.deepEqual(buildKnowledgeDatabasePayload({ database_name: ' 库 ', description: '说明', kb_type, embed_model_id: 'embed', enable_knowledge_graph: false }), { database_name: '库', description: '说明', kb_type: 'milvus', embed_model_id: 'embed', enable_knowledge_graph: false })
}
assert.deepEqual(buildKnowledgeDatabasePayload({ database_name: '图谱库', embed_model_id: 'embed', enable_knowledge_graph: true, llm_model_id: 'chat' }), { database_name: '图谱库', description: '', kb_type: 'milvus', embed_model_id: 'embed', enable_knowledge_graph: true, llm_model_id: 'chat' })

assert.deepEqual(validateChunkParams({ chunk_size: 1000, chunk_overlap: 200 }), { chunk_size: 1000, chunk_overlap: 200 })
assert.deepEqual(validateChunkParams({ chunk_size: 4000, chunk_overlap: 1000 }), { chunk_size: 4000, chunk_overlap: 1000 })
assert.deepEqual(validateChunkParams({ chunk_size: 100, chunk_overlap: 99 }), { chunk_size: 100, chunk_overlap: 99 })
for (const invalid of [{ chunk_size: 0, chunk_overlap: 0 }, { chunk_size: 1000, chunk_overlap: 1000 }, { chunk_size: 100, chunk_overlap: -1 }, { chunk_size: 1.5, chunk_overlap: 0 }, { chunk_size: 4001, chunk_overlap: 0 }, { chunk_size: 4000, chunk_overlap: 1001 }]) assert.throws(() => validateChunkParams(invalid))

const dangerous = '<img src=x onerror=alert(1)>'
const graph = normalizeKnowledgeGraph({ nodes: [{ id: 'a', entity_name: dangerous, properties: { description: '<script>bad()</script>' } }, { id: 'b', entity_name: 'B' }], edges: [{ source: 'a', target: 'b', type: dangerous }, { source: 'missing', target: 'b' }], is_truncated: true })
assert.equal(graph.nodes[0].name, dangerous)
assert.equal(graph.links.length, 1)
assert.equal(graph.truncated, true)
assert.equal(graph.nodes[0].tooltipText.includes('<script>bad()</script>'), true)
assert.equal(graph.nodes[0].tooltipText.includes('onerror'), true)
assert.deepEqual(normalizeKnowledgeGraph({ nodes: [{ id: 'a' }, { id: 'a' }, {}], edges: [] }).nodes.map(node => node.id), ['a'])
const params = buildKnowledgeGraphParams({ node_label: 'A&B/#中文', max_depth: 99, max_nodes: 9999 })
assert.deepEqual(params, { node_label: 'A&B/#中文', max_depth: 5, max_nodes: 200 })
assert.equal(new URLSearchParams(params).get('node_label'), 'A&B/#中文')
assert.match(new URLSearchParams(params).toString(), /%26B%2F%23/)

assert.deepEqual(resetKnowledgeSelection('kb-2'), { dbId: 'kb-2', folderId: null, tab: 'documents', queryResult: null })
const folders = [{ id: 'a', parent_id: null, name: 'A' }, { id: 'b', parent_id: 'a', name: 'B' }]
assert.deepEqual(knowledgeFolderTrail(folders, 'b').map(folder => folder.id), ['a', 'b'])
assert.deepEqual(knowledgeFolderTrail([{ id: 'loop', parent_id: 'loop' }], 'loop').map(folder => folder.id), ['loop'])
const docs = [{ id: '1', file_name: 'root.txt', folder_id: null }, { id: '2', file_name: 'child.md', folder_id: 'a' }]
assert.deepEqual(filterKnowledgeDocuments(docs, { folderId: null }).map(doc => doc.id), ['1'])
assert.deepEqual(filterKnowledgeDocuments(docs, { folderId: 'a', search: 'CHILD' }).map(doc => doc.id), ['2'])
assert.equal(normalizeKnowledgeQuery({ result: dangerous }).answer, dangerous)
assert.equal(normalizeKnowledgeQuery({ result: [{ content: dangerous, score: 0.75, metadata: { source: '<b>source</b>' } }] }).items[0].source, '<b>source</b>')
assert.deepEqual(buildKnowledgeLocation('kb /中文', 'query'), {
  path: '/ai/knowledge',
  query: { dbId: 'kb /中文', view: 'query' },
})
assert.equal(normalizeKnowledgeView('documents', false), 'documents')
assert.equal(normalizeKnowledgeView('query', false), 'query')
assert.equal(normalizeKnowledgeView('graph', true), 'graph')
assert.equal(normalizeKnowledgeView('graph', false), 'documents')
assert.equal(normalizeKnowledgeView('unknown', true), 'documents')
console.log('ok knowledge transforms: business failures, graph safety, request bounds, folders and query results')
