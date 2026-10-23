import assert from 'node:assert/strict'
import * as chatFiles from '../../plugins/ai/utils/chatFiles.js'

assert.equal(typeof chatFiles.chatImagePreviewBlob, 'function', 'Protected octet-stream image downloads need bounded raster signature detection')
const samples = [
  ['image/png', [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]],
  ['image/jpeg', [0xff, 0xd8, 0xff, 0xe0]],
  ['image/gif', new TextEncoder().encode('GIF89a')],
  ['image/gif', new TextEncoder().encode('GIF87a')],
  ['image/webp', new TextEncoder().encode('RIFF1234WEBP')],
]
for (const [expectedType, bytes] of samples) {
  const blob = new Blob([new Uint8Array(bytes)], { type: 'application/octet-stream' })
  const preview = await chatFiles.chatImagePreviewBlob(blob)
  assert.equal(preview.type, expectedType)
  assert.deepEqual(new Uint8Array(await preview.arrayBuffer()), new Uint8Array(bytes))
}
for (const content of ['<html><script>alert(1)</script></html>', '<svg xmlns="http://www.w3.org/2000/svg"/>', '', 'GIF88a', 'RIFF1234WAVE']) {
  await assert.rejects(chatFiles.chatImagePreviewBlob(new Blob([content], { type: 'image/png' })), /无法作为图片预览/)
}
const requestedSlices = []
const untrustedBlob = {
  slice(start, end) {
    requestedSlices.push([start, end])
    return new Blob(['not an image'])
  },
}
await assert.rejects(chatFiles.chatImagePreviewBlob(untrustedBlob), /无法作为图片预览/)
assert.deepEqual(requestedSlices, [[0, 12]], 'Inspect only the first 12 bytes, even for a large downloaded file')
