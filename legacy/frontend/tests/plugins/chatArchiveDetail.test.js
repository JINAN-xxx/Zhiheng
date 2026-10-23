import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { ref, reactive, watch, nextTick } from 'vue'

// Run the actual detail loader and status watcher with delayed network replies.
const source = readFileSync(new URL('../../plugins/ai/views/chat/index.vue', import.meta.url), 'utf8')
const functions = source.slice(source.indexOf('async function openSession(id)'), source.indexOf('function resetFileDraft()'))
const watcherStart = source.indexOf('watch(() => workspaceStore.sessionChange')
const watcher = source.slice(watcherStart, source.indexOf('\nwatch(draft', watcherStart))

async function checkDetailRace({ additionalMutation = false, preexistingChange = false } = {}) {
  let resolveDetail
  const detail = new Promise(resolve => { resolveDetail = resolve })
  const scope = {
    watch, sessionId: ref(null), sessionStatus: ref('active'), sessionLoading: ref(false),
    loading: ref(false), modelId: ref('bound-model'), messages: ref([]), selectedSkills: ref([]),
    skillPickerOpen: ref(false), runState: ref('idle'),
    workspaceStore: reactive({ sessionChange: null, sessionStatusChanges: {} }),
    getChatSession: () => detail,
    scrollToBottom: async () => {},
    resetFileDraft: () => {},
  }
  const confirmStatus = (sessionId, status) => {
    const change = { sessionId, status }
    scope.workspaceStore.sessionChange = change
    scope.workspaceStore.sessionStatusChanges[sessionId] = change
  }
  if (preexistingChange) confirmStatus('archive-example', 'active')
  vm.createContext(scope)
  vm.runInContext('let sessionLoadGeneration = 0;\n' + functions + '\n' + watcher, scope)

  const pending = scope.openSession('archive-example')
  if (!preexistingChange) confirmStatus('archive-example', 'active')
  await nextTick()
  if (additionalMutation) {
    confirmStatus('other-session', 'active')
    await nextTick()
  }
  resolveDetail({ data: { session: { status: 'archived', modelId: 'bound-model' }, messages: [] } })
  await pending
  await nextTick()
  assert.equal(scope.sessionId.value, 'archive-example')
  assert.equal(scope.sessionStatus.value, preexistingChange ? 'archived' : 'active',
    'Only status changes confirmed after a detail request begins should override that response')
}

await checkDetailRace()
await checkDetailRace({ additionalMutation: true })
await checkDetailRace({ preexistingChange: true })
console.log('ok archived detail: confirmed restore wins over stale GET, fresh server detail wins over older local state')
