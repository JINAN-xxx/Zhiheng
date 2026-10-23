import assert from 'node:assert/strict'
import { createChatFileQueue, chatFilePayload, chatImageHint, chatFileLabel, chatFileDownloadPath, CHAT_FILE_ACCEPT } from '../../plugins/ai/utils/chatFiles.js'

const mb = 1024 * 1024
const file = (name, size = 100, type = '') => ({ name, size, type })
const settings = { visionEnabled: true, imageMaxSizeMb: 10, model: { capabilities: { vision: true } } }
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function harness() {
  const calls = [], revoked = [], updates = []
  const queue = createChatFileQueue({
    upload(file, options) {
      const result = deferred()
      calls.push({ file, options, ...result })
      return result.promise
    },
    createObjectURL: file => `blob:${file.name}`,
    revokeObjectURL: url => revoked.push(url),
    onChange: items => updates.push(items),
  })
  return { queue, calls, revoked, updates }
}

// Reserve all batch slots synchronously, even before the first upload completes.
{
  const { queue, calls } = harness()
  const first = queue.add(Array.from({ length: 7 }, (_, i) => file(`${i}.pdf`)), settings)
  assert.equal(queue.items.length, 5)
  assert.equal(calls.length, 5)
  assert.equal(first.rejected.length, 2)
  assert.match(first.rejected[0].error, /最多.*5/)
  assert.equal(queue.add([file('later.docx')], settings).rejected.length, 1)
  calls.forEach((call, i) => call.resolve({ code: 200, fileId: `doc-${i}` }))
  await first.uploads
  assert.ok(queue.items.every(item => item.status === 'ready'))
  assert.deepEqual(chatFilePayload(queue.items), { images: [], attachments: Array.from({ length: 5 }, (_, i) => ({ fileId: `doc-${i}` })) })
}

{
  const { queue, calls } = harness()
  const batch = queue.add(Array.from({ length: 11 }, (_, i) => file(`${i}.png`, 2 * mb, 'image/png')), settings)
  assert.equal(calls.length, 10, 'Images over 1 MB should be accepted within the preference limit')
  assert.equal(batch.rejected.length, 1)
  assert.match(batch.rejected[0].error, /最多.*10/)
  assert.equal(queue.add([file('notes.MD')], settings).rejected.length, 0)
  calls.forEach((call, i) => call.resolve({ code: 200, fileId: `file-${i}`, originalFilename: call.file.name }))
  await batch.uploads
  assert.equal(chatFilePayload(queue.items).images.length, 10)
}

for (const extension of ['pdf', 'docx', 'txt', 'md', 'xlsx', 'csv']) {
  assert.ok(CHAT_FILE_ACCEPT.includes(`.${extension}`))
  const { queue, calls } = harness()
  const batch = queue.add([file(`document.${extension}`, 20 * mb), file(`large.${extension}`, 20 * mb + 1)], settings)
  assert.equal(calls.length, 1)
  assert.match(batch.rejected[0].error, /20 MB/)
  calls[0].resolve({ code: 200, fileId: extension })
  await batch.uploads
}

{
  const { queue } = harness()
  assert.match(queue.add([file('large.png', 10 * mb + 1)], settings).rejected[0].error, /10 MB/)
  assert.match(queue.add([file('large.png', 50 * mb + 1)], { ...settings, imageMaxSizeMb: 100 }).rejected[0].error, /50 MB/)
  assert.match(queue.add([file('script.svg')], settings).rejected[0].error, /不支持/)
  assert.match(queue.add([file('empty.txt', 0)], settings).rejected[0].error, /空文件/)
  assert.match(chatImageHint({ ...settings, visionEnabled: false }), /对话偏好/)
  assert.match(chatImageHint({ ...settings, model: { capabilities: { vision: false } } }), /模型.*视觉/)
  assert.match(chatImageHint({ ...settings, model: {} }), /模型.*视觉/)
  assert.equal(chatImageHint(settings), '')
  assert.match(queue.add([file('photo.png')], { ...settings, visionEnabled: false }).rejected[0].error, /对话偏好/)
  assert.equal(queue.items.length, 0)
}

// Failures remain visible, block payload construction, and can retry the same file.
{
  const { queue, calls, updates } = harness()
  const batch = queue.add([file('report.pdf')], settings)
  calls[0].options.onUploadProgress({ loaded: 45, total: 100 })
  assert.equal(queue.items[0].progress, 45)
  assert.equal(updates.at(-1)[0].progress, 45)
  calls[0].reject(new Error('文件存储暂时不可用'))
  await batch.uploads
  assert.equal(queue.items[0].status, 'failed')
  assert.equal(queue.items[0].error, '文件存储暂时不可用')
  assert.throws(() => chatFilePayload(queue.items), /重试.*移除/)
  const retry = queue.retry(queue.items[0].id)
  assert.equal(queue.items[0].status, 'uploading')
  calls[1].resolve({ code: 200, fileId: 'retried-file', originalFilename: 'report.pdf' })
  await retry
  assert.deepEqual(chatFilePayload(queue.items), { images: [], attachments: [{ fileId: 'retried-file' }] })
}

// Never trust a successful HTTP transport with failed application status or missing file ID.
for (const response of [{ code: 500, msg: '存储容量不足', fileId: 'bad' }, { code: 200 }, { fileId: 'missing-code' }]) {
  const { queue, calls } = harness()
  const batch = queue.add([file('report.txt')], settings)
  calls[0].resolve(response)
  await batch.uploads
  assert.equal(queue.items[0].status, 'failed')
  if (response.msg) assert.equal(queue.items[0].error, response.msg)
}

// Abort and release previews immediately; ignore responses from the previous session.
{
  const { queue, calls, revoked } = harness()
  const batch = queue.add([file('old.png')], settings)
  queue.clear()
  assert.equal(calls[0].options.signal.aborted, true)
  assert.deepEqual(revoked, ['blob:old.png'])
  const fresh = queue.add([file('new.txt')], settings)
  calls[0].resolve({ code: 200, fileId: 'old' })
  calls[1].resolve({ code: 200, fileId: 'new' })
  await Promise.all([batch.uploads, fresh.uploads])
  assert.deepEqual(queue.items.map(item => item.fileId), ['new'])
  assert.equal(queue.items[0].fileName, 'new.txt')
  queue.remove(queue.items[0].id)
  assert.deepEqual(queue.items, [])
}

{
  const { queue, calls, revoked } = harness()
  const batch = queue.add([file('removed.png')], settings)
  queue.remove(queue.items[0].id)
  assert.equal(calls[0].options.signal.aborted, true)
  calls[0].resolve({ code: 200, fileId: 'removed' })
  await batch.uploads
  assert.equal(queue.items.length, 0)
  assert.deepEqual(revoked, ['blob:removed.png'])
}

assert.equal(chatFileLabel({ fileId: 'secret-id' }, 'image'), '图片')
assert.equal(chatFileLabel({ fileName: '产品手册.pdf' }), '产品手册.pdf')
assert.equal(chatFileDownloadPath({ fileId: 'private/id', fileName: '../name ?#.txt' }), '/common/files/private%2Fid/download/..%2Fname%20%3F%23.txt')
assert.equal(chatFileDownloadPath({ fileId: 'legacy' }), '/common/files/legacy/download/attachment')
