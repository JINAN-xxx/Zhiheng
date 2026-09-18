import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, reactive, ref } from 'vue'
import * as knowledge from '../../plugins/ai/utils/knowledge.js'

// Execute the page's real actions with controlled network replies and timers.
const source = readFileSync(new URL('../../plugins/ai/views/knowledge/index.vue', import.meta.url), 'utf8')
const script = source.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1].replace(/^import[\s\S]*?from ['"][^'"]+['"];?\n/gm, '')
const config = { llm_model_id: 'chat', reasoning_strategy: 'auto', timeout_seconds: 240, max_output_tokens: 16384, concurrency: 2 }
const response = data => ({ code: 200, data })
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function workspace(overrides = {}, editable = true) {
  const calls = [], timers = new Map()
  let timerSequence = 0
  const route = reactive({ query: { dbId: 'kb-a', view: 'graph' } })
  const api = {
    listKnowledgeTypes: async () => response({ graph_available: true }),
    listKnowledgeModels: async () => response({ models: [{ model_id: 'chat', display_name: '对话模型' }] }),
    listKnowledgeDocuments: async () => response({ documents: [{ id: 'doc-a', file_name: '说明.md' }] }),
    listKnowledgeGraphLabels: async () => response({ labels: ['实体'] }),
    getKnowledgeSubgraph: async () => { calls.push(['graph']); return response({ nodes: [], edges: [] }) },
    getKnowledgeGraphBuild: async () => response({ status: 'idle', graph_readable: false }),
    getKnowledgeGraphConfig: async () => response({ config, requires_rebuild: false, can_resume: false }),
    getKnowledgeGraphPreview: async () => response({ status: 'idle' }),
    saveKnowledgeGraphConfig: async (dbId, data) => { calls.push(['save', dbId, data]); return response({ config: data, requires_rebuild: true, can_resume: false }) },
    startKnowledgeGraphBuild: async (dbId, data) => { calls.push(['build', dbId, data]); return response({ status: 'queued', graph_readable: false }) },
    startKnowledgeGraphPreview: async (dbId, data) => { calls.push(['preview', dbId, data]); return response({ status: 'queued', file_name: '说明.md' }) },
    ...overrides,
  }
  const scope = { ...knowledge, computed, reactive, ref, watch: () => {}, onMounted: () => {}, onBeforeUnmount: () => {},
    api, AbortController, useRoute: () => route, useRouter: () => ({}),
    useUserStore: () => ({ permissions: editable ? ['*:*:*'] : ['ai:knowledge:query'] }),
    normalizeErrorMessage: error => error.message || String(error), feedback: { msgSuccess() {} },
    setTimeout: callback => { const id = ++timerSequence; timers.set(id, callback); return id }, clearTimeout: id => timers.delete(id),
  }
  vm.createContext(scope)
  vm.runInContext(script, scope)
  assert.equal(typeof scope.startGraphPreview, 'function', 'the graph page needs a background preview action')
  vm.runInContext(`databases.value = [{ db_id: 'kb-a', name: '知识库A', enable_knowledge_graph: true }, { db_id: 'kb-b', name: '知识库B' }]; selection.dbId = 'kb-a'; selection.tab = 'graph'; graphAvailable.value = true; graphConfigReady.value = true; graphConfig.value = normalizeKnowledgeGraphConfig({ config: ${JSON.stringify(config)} });`, scope)
  const state = vm.runInContext('({ graphConfig, graphPreview, graphPreviewError, graphPreviewStarting, graphConfigSaving, graphStarting, graphBuild, graphBuildError, graphReadable, graphBuildLoading, selection, databases })', scope)
  return { ...scope, state, calls, timers, route, evaluate: code => vm.runInContext(code, scope) }
}

const changed = { ...config, reasoning_strategy: 'low' }
{
  const page = workspace()
  await page.startGraphBuild({ config: changed })
  assert.deepEqual(page.calls.map(call => call[0]), ['save', 'build'], 'a changed form must be saved before starting a build')
  assert.equal(page.calls[1][2].rebuild, true, 'the save response decides if a new version is required')
}
{
  const page = workspace()
  page.state.graphConfig.value = knowledge.normalizeKnowledgeGraphConfig({ config, requires_rebuild: true, can_resume: true })
  page.state.graphBuild.value = knowledge.normalizeKnowledgeGraphBuild({ status: 'interrupted' })
  await page.startGraphBuild({ config })
  assert.equal(page.calls[0][0], 'build')
  assert.equal(Object.keys(page.calls[0][2]).length, 0, 'resuming uses the saved unfinished version')
}
{
  const page = workspace({ saveKnowledgeGraphConfig: async () => { throw new Error('保存失败') } })
  await page.startGraphBuild({ config: changed })
  assert.equal(page.calls.length, 0, 'failed configuration saves must not start a build')
  assert.match(page.state.graphBuildError.value, /保存失败/)
}
{
  const page = workspace()
  await page.startGraphPreview({ config: changed, document_id: 'doc-a' })
  assert.deepEqual(page.calls.map(call => call[0]), ['preview'], 'a preview can test a draft without saving it')
  assert.equal(page.calls[0][2].config.reasoning_strategy, 'low')
  assert.equal(page.calls[0][2].document_id, 'doc-a')
  await page.saveGraphConfig(config)
  await page.startGraphBuild({ config })
  await page.startGraphPreview({ config })
  assert.equal(page.calls.length, 1, 'a running preview blocks conflicting mutations')
  assert.equal(page.timers.size, 1)
  page.stopGraphBuildPolling()
  assert.equal(page.timers.size, 0, 'leaving the page stops preview polling too')
}
{
  const page = workspace({}, false)
  await page.saveGraphConfig(config)
  await page.startGraphBuild({ config, rebuild: true })
  await page.startGraphPreview({ config })
  assert.equal(page.calls.length, 0, 'read-only members must not invoke graph mutations')
}
{
  const pending = deferred()
  let signal
  const page = workspace({ getKnowledgeGraphPreview: async (_dbId, requestSignal) => { signal = requestSignal; return pending.promise } })
  const request = page.refreshGraphPreview()
  page.resetKnowledgeContext('kb-b')
  page.route.query.dbId = 'kb-b'
  assert.equal(signal.aborted, true)
  pending.resolve(response({ status: 'completed', file_name: '旧知识库.md', result: { entities: 99 } }))
  await request
  assert.equal(page.state.graphPreview.value.status, 'idle', 'late preview results cannot enter a different knowledge base')
  assert.equal(page.timers.size, 0)
}
{
  const pending = deferred()
  const page = workspace({ saveKnowledgeGraphConfig: () => pending.promise })
  const request = page.startGraphBuild({ config: changed })
  page.resetKnowledgeContext('kb-b')
  page.route.query.dbId = 'kb-b'
  pending.resolve(response({ config: changed, requires_rebuild: true }))
  await request
  assert.equal(page.calls.length, 0, 'navigation during save must prevent a later dependent build')
  assert.equal(page.state.graphConfig.value.config, null)
}
{
  const pending = deferred()
  const page = workspace({ getKnowledgeGraphConfig: () => pending.promise })
  const refresh = page.refreshGraphConfig()
  await page.saveGraphConfig(changed)
  pending.resolve(response({ config }))
  await refresh
  assert.equal(page.state.graphConfig.value.config.reasoning_strategy, 'low', 'an older configuration GET cannot overwrite a confirmed save')
}
{
  const page = workspace({ getKnowledgeGraphPreview: async () => response({ status: 'running', progress: { stage: 'extracting', extracted_chunks: 1, total_chunks: 4 } }) })
  await page.loadGraphWorkspace()
  assert.equal(page.state.graphPreview.value.status, 'running')
  assert.equal(page.timers.size, 1, 'returning to a running preview resumes polling')
  assert.equal(page.state.graphBuildLoading.value, false)
}
{
  const page = workspace()
  page.state.graphBuild.value = knowledge.normalizeKnowledgeGraphBuild({ status: 'running', active_revision: 'previous', graph_readable: true })
  await page.loadGraph()
  assert.equal(page.calls.filter(call => call[0] === 'graph').length, 1, 'rebuilding a new version retains access to the active version')
  page.state.graphBuild.value = knowledge.normalizeKnowledgeGraphBuild({ status: 'running', active_revision: 'previous', graph_readable: false })
  await page.loadGraph()
  assert.equal(page.calls.filter(call => call[0] === 'graph').length, 1, 'server unreadability prevents reads during an in-place write')
}
console.log('ok graph workspace: save-before-build, resume, permissions, task exclusion, route races and preview recovery')
