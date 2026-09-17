import assert from 'node:assert/strict'
import * as knowledge from '../../plugins/ai/utils/knowledge.js'

assert.equal(typeof knowledge.normalizeKnowledgeGraphBuild, 'function', 'graph jobs need a parser that preserves failed job state')
const { normalizeKnowledgeGraphBuild, buildKnowledgeGraphBuildPayload, isKnowledgeGraphBuilding, knowledgeGraphProgress } = knowledge
const failed = normalizeKnowledgeGraphBuild({ code: 200, data: { status: 'failed', job_id: 'job-1', total_documents: 3, processed_documents: 3, failed_documents: 1, error: '模型不可用' } })
assert.equal(failed.status, 'failed')
assert.equal(failed.error, '模型不可用')
assert.equal(failed.failed_documents, 1)
const structured = normalizeKnowledgeGraphBuild({ code: 200, data: { status: 'running', current_document: { id: 'doc-1', file_name: '手册.md' }, error: { code: 'model_unavailable', message: '模型暂不可用' } } })
assert.equal(structured.current_document, '手册.md')
assert.equal(structured.error, '模型暂不可用')
assert.equal(knowledgeGraphProgress(failed), 100)
assert.equal(knowledgeGraphProgress({ total_documents: 3, processed_documents: 2, failed_documents: 1 }), 67)
assert.equal(isKnowledgeGraphBuilding(failed), false)
for (const status of ['disabled', 'idle', 'queued', 'running', 'completed', 'failed', 'interrupted']) {
  const job = normalizeKnowledgeGraphBuild({ code: 200, data: { status } })
  assert.equal(job.status, status)
  assert.equal(isKnowledgeGraphBuilding(job), ['queued', 'running'].includes(status))
}
assert.throws(() => normalizeKnowledgeGraphBuild({ code: 403, msg: '无权查看' }), /无权查看/)
assert.throws(() => normalizeKnowledgeGraphBuild({ code: 200, data: { status: 'unknown' } }), /构建状态无效/)
assert.equal(knowledgeGraphProgress({ total_documents: 0, processed_documents: 0, failed_documents: 0 }), 0)
assert.equal(knowledgeGraphProgress({ total_documents: 0, status: 'completed' }), 100)
assert.equal(knowledgeGraphProgress({ total_documents: 3, processed_documents: 1, failed_documents: 0 }), 33)
assert.equal(knowledgeGraphProgress({ total_documents: 1, processed_documents: 2, failed_documents: 0 }), 100)
assert.throws(() => buildKnowledgeGraphBuildPayload({ enable_knowledge_graph: false }), /对话模型/)
assert.deepEqual(buildKnowledgeGraphBuildPayload({ enable_knowledge_graph: false }, 'chat-model'), { llm_model_id: 'chat-model' })
assert.deepEqual(buildKnowledgeGraphBuildPayload({ enable_knowledge_graph: true, llm_model_id: 'original' }, 'other'), {}, 'backfill must retain the configured model')
console.log('ok graph build: failure state, model retention, active state and progress')
