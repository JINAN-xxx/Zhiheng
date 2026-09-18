import assert from 'node:assert/strict'
import * as knowledge from '../../plugins/ai/utils/knowledge.js'

assert.equal(typeof knowledge.buildKnowledgeGraphConfigPayload, 'function', 'graph configuration must validate a complete form before saving or testing')
const { buildKnowledgeGraphConfigPayload: config, normalizeKnowledgeGraphConfig, normalizeKnowledgeGraphPreview, knowledgeGraphStage, isKnowledgeGraphReadable, buildKnowledgeGraphBuildPayload } = knowledge
const defaults = { llm_model_id: 'chat', reasoning_strategy: 'auto', timeout_seconds: 240, max_output_tokens: 16384, concurrency: 2 }
assert.deepEqual(config({ llm_model_id: 'chat' }), defaults)
assert.deepEqual(config({ ...defaults, workspace: '/private', api_key: 'secret' }), defaults, 'only user-configurable fields may reach the API')
for (const strategy of ['auto', 'default', 'low', 'disabled']) assert.equal(config({ ...defaults, reasoning_strategy: strategy }).reasoning_strategy, strategy)
assert.throws(() => config({ ...defaults, llm_model_id: '' }), /对话模型/)
assert.throws(() => config({ ...defaults, reasoning_strategy: 'high' }), /推理策略/)
for (const [field, values] of Object.entries({ timeout_seconds: [29, 601, 30.5, null], max_output_tokens: [1023, 32769, 1024.5, null], concurrency: [0, 5, 1.5, null] })) {
  for (const value of values) assert.throws(() => config({ ...defaults, [field]: value }), /整数/)
}
const saved = normalizeKnowledgeGraphConfig({ code: 200, data: { config: defaults, active_config: { ...defaults, llm_model_id: 'old' }, active_revision: 'r1', requires_rebuild: true, can_resume: true } })
assert.deepEqual(saved.config, defaults)
assert.equal(saved.active_config.llm_model_id, 'old')
assert.equal(saved.requires_rebuild, true)
assert.equal(saved.can_resume, true)
assert.equal(normalizeKnowledgeGraphConfig({ config: null }).config, null)
assert.throws(() => normalizeKnowledgeGraphConfig({ code: 403, msg: '无权访问' }), /无权访问/)
assert.deepEqual(buildKnowledgeGraphBuildPayload({ enable_knowledge_graph: true }, undefined, { ...saved, rebuild: false }), {}, 'an unfinished matching version can resume even while a previous version is active')
assert.deepEqual(buildKnowledgeGraphBuildPayload({ enable_knowledge_graph: true }, undefined, { ...saved, can_resume: false }), { rebuild: true }, 'changed settings must start a version when there is nothing to resume')
assert.deepEqual(buildKnowledgeGraphBuildPayload({ enable_knowledge_graph: true }, undefined, { rebuild: true }), { rebuild: true })
assert.equal(isKnowledgeGraphReadable({ graph_readable: true }, { status: 'failed' }), true)
assert.equal(isKnowledgeGraphReadable({}, { status: 'running', active_revision: 'r1' }), true)
assert.equal(isKnowledgeGraphReadable({ graph_readable: false }, { status: 'running', active_revision: null }), false)
assert.equal(isKnowledgeGraphReadable({ graph_readable: true }, { status: 'running', active_revision: 'r1', graph_readable: false }), false, 'the latest server readability flag blocks reads during writes to the same version')
assert.equal(isKnowledgeGraphReadable({ graph_readable: false }, { status: 'completed', graph_readable: true }), true, 'fresh job state supersedes an older database summary')
assert.equal(isKnowledgeGraphReadable({}, { status: 'completed' }), true, 'legacy completed jobs remain readable')
const preview = normalizeKnowledgeGraphPreview({ code: 200, data: { status: 'completed', file_name: '说明.md', result: { entities: 4, relations: 3, chunks: 2, seconds: 12.5 }, progress: { stage: 'completed', total_chunks: 2, extracted_chunks: 2 } } })
assert.equal(preview.file_name, '说明.md')
assert.deepEqual(preview.result, { entities: 4, relations: 3, chunks: 2, seconds: 12.5 })
const failed = normalizeKnowledgeGraphPreview({ status: 'failed', error: { code: 'model_timeout', message: '模型响应超时' } })
assert.equal(failed.error, '模型响应超时')
assert.equal(failed.error_code, 'model_timeout')
assert.throws(() => normalizeKnowledgeGraphPreview({ status: 'unknown' }), /试构建状态无效/)
for (const stage of ['preparing', 'extracting', 'graph_indexing', 'embedding', 'persisting', 'completed', 'failed']) {
  assert.ok(knowledgeGraphStage({ stage }), `stage ${stage} needs a user-facing label`)
  assert.notEqual(knowledgeGraphStage({ stage }), stage)
}
assert.equal(knowledgeGraphStage({ stage: 'unrecognized' }), '', 'do not invent progress for an unknown backend phase')
console.log('ok graph configuration: strict settings, preview results, active versions and resume/rebuild decisions')
