import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, ref, nextTick } from 'vue'
import { beginSend } from '../../plugins/ai/utils/chatTransforms.js'
import { chatFilePayload } from '../../plugins/ai/utils/chatFiles.js'
import { normalizeErrorMessage } from '../../src/utils/errorMessage.js'

const source = readFileSync(new URL('../../plugins/ai/views/chat/index.vue', import.meta.url), 'utf8')
const sendSource = source.slice(source.indexOf('async function send()'), source.indexOf('function handleEvent('))
const readyFiles = [
  { id: 'local-image', fileId: 'image-file', kind: 'image', fileName: '照片.png', mimeType: 'image/png', sizeBytes: 2048, status: 'ready' },
  { id: 'local-doc', fileId: 'document-file', kind: 'attachment', fileName: '手册.pdf', mimeType: 'application/pdf', sizeBytes: 1024, status: 'ready' },
]
const normalize = value => JSON.parse(JSON.stringify(value))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function harness(initialDraft = '总结这些文件') {
  const requests = [], errors = []
  let sequence = 0
  const scope = {
    AbortController, beginSend, chatFilePayload, normalizeErrorMessage, nextTick,
    draft: ref(initialDraft), fileItems: ref(readyFiles.map(file => ({ ...file }))),
    runId: ref('previous-run'), runState: ref('completed'), modelId: ref('vision-model'), sessionId: ref('existing-session'),
    selectedSkills: ref([{ skillSlug: 'summary', skillName: '总结' }]), skillPickerOpen: ref(false),
    messages: ref([]), loading: ref(false), activeAssistantId: ref(null), abortController: ref(null),
    proxy: { $modal: { msgError: error => errors.push(error) } },
    uuidv4: () => `message-${++sequence}`,
    scrollToBottom: async () => {}, workspaceStore: { loadSessions: async () => {} },
    createChatStream(payload) {
      const pending = deferred()
      requests.push({ payload: normalize(payload), ...pending })
      return pending.promise
    },
    handleEvent() {},
  }
  scope.canSend = computed(() => !scope.loading.value && Boolean(scope.draft.value.trim() || scope.fileItems.value.length)
    && scope.fileItems.value.every(file => file.status === 'ready'))
  scope.fileQueue = { clear() { scope.fileItems.value = [] } }
  vm.createContext(scope)
  vm.runInContext('let sessionLoadGeneration = 0;\n' + sendSource, scope)
  return { scope, requests, errors }
}
const flushRequest = async requests => {
  for (let count = 0; !requests.length && count < 20; count++) await Promise.resolve()
  assert.ok(requests.length)
}

{
  const { scope, requests, errors } = harness()
  const pending = scope.send()
  await flushRequest(requests)
  assert.equal(scope.draft.value, '总结这些文件', 'Keep the draft while the initial request is pending')
  assert.equal(scope.fileItems.value.length, 2)
  assert.equal(scope.runId.value, null)
  await scope.send()
  assert.equal(requests.length, 1, 'Double send must not create a second request')
  requests[0].reject(new Error('暂时无法读取附件'))
  await pending
  assert.equal(scope.draft.value, '总结这些文件')
  assert.equal(scope.fileItems.value.length, 2)
  assert.equal(scope.selectedSkills.value.length, 1)
  assert.equal(scope.messages.value.length, 0, 'Rejected requests must not leave duplicate local messages')
  assert.equal(scope.loading.value, false)
  assert.deepEqual(errors, ['暂时无法读取附件'])
  const retry = scope.send()
  while (requests.length < 2) await Promise.resolve()
  requests[1].resolve((async function* () {})())
  await retry
  assert.deepEqual(requests[1].payload.images, [{ fileId: 'image-file' }])
  assert.deepEqual(requests[1].payload.attachments, [{ fileId: 'document-file' }])
  assert.equal(scope.draft.value, '')
  assert.equal(scope.fileItems.value.length, 0)
  assert.equal(scope.selectedSkills.value.length, 0)
  assert.deepEqual(normalize(scope.messages.value[0].attachments), [{ fileId: 'document-file', fileName: '手册.pdf', mimeType: 'application/pdf', sizeBytes: 1024 }])
}

{
  const { scope, requests } = harness('   ')
  const pending = scope.send()
  await flushRequest(requests)
  assert.equal(requests[0].payload.message, '请分析上传的文件。')
  requests[0].resolve((async function* () {})())
  await pending
}

for (const status of ['uploading', 'failed']) {
  const { scope, requests } = harness()
  scope.fileItems.value[0].status = status
  await scope.send()
  assert.equal(requests.length, 0, `${status} files must not silently disappear from a sent message`)
}
