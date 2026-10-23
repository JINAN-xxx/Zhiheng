import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, reactive, ref, watch, nextTick } from 'vue'
import * as knowledge from '../../plugins/ai/utils/knowledge.js'

const source = readFileSync(new URL('../../plugins/ai/views/knowledge/components/KnowledgeGraphPage.vue', import.meta.url), 'utf8')
const script = source.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1].replace(/^import[\s\S]*?from ['"][^'"]+['"];?\n/gm, '')
const config = { llm_model_id: 'chat', reasoning_strategy: 'auto', timeout_seconds: 240, max_output_tokens: 16384, concurrency: 2 }
const props = reactive({ database: { db_id: 'kb-a', enable_knowledge_graph: true }, job: { status: 'failed' }, available: true,
  canEdit: true, configReady: true, configState: { config, active_revision: 'old', requires_rebuild: true, can_resume: true },
  preview: { status: 'idle' }, documents: [], chatOptions: [{ value: 'chat', label: '对话模型' }], labels: [],
})
const emitted = []
const scope = { ...knowledge, computed, reactive, ref, watch, defineProps: () => props, defineEmits: () => (...args) => emitted.push(args) }
vm.createContext(scope)
vm.runInContext(script, scope)
assert.equal(typeof scope.startBuild, 'function', 'graph page actions need complete form validation')
const state = vm.runInContext('({ configForm, controlsDisabled, dirty, buildAction, previewDocumentId })', scope)
assert.equal(state.configForm.reasoning_strategy, 'auto')
assert.equal(state.controlsDisabled.value, false, 'an enabled graph can still change its model')
assert.equal(state.buildAction.value, '续建图谱')
state.configForm.reasoning_strategy = 'low'
assert.equal(state.dirty.value, true)
scope.startBuild()
assert.equal(emitted.at(-1)[1].config.reasoning_strategy, 'low', 'the unsaved form accompanies the build action')
scope.startBuild(true)
assert.equal(emitted.at(-1)[1].rebuild, true)
scope.previewGraph()
assert.equal(emitted.at(-1)[0], 'preview')
assert.equal(emitted.at(-1)[1].config.reasoning_strategy, 'low')
const previous = emitted.length
props.canEdit = false
scope.startBuild()
scope.saveConfig()
scope.previewGraph()
assert.equal(state.controlsDisabled.value, true)
assert.equal(emitted.length, previous, 'read-only actions cannot emit mutations')
props.canEdit = true
props.preview.status = 'running'
assert.equal(state.controlsDisabled.value, true)
scope.startBuild()
assert.equal(emitted.length, previous)
props.preview.status = 'idle'
state.configForm.timeout_seconds = 0
scope.previewGraph()
assert.equal(emitted.length, previous, 'invalid draft settings never leave the component')
props.database = { db_id: 'kb-b', enable_knowledge_graph: false }
props.configState = { config: null }
await nextTick()
assert.equal(state.configForm.llm_model_id, '')
assert.equal(state.configForm.reasoning_strategy, 'auto')
assert.equal(state.previewDocumentId.value, undefined)
console.log('ok graph page: editable saved settings, complete drafts, explicit rebuilds, readonly and busy guards')
