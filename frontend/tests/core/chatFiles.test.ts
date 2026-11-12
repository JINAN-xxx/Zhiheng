import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  CHAT_ATTACHMENTS_MAX_BYTES,
  createChatFileQueue,
  imagePreviewBlob
} from '../../src/modules/chat/attachments.ts'
import type { ManagedFile } from '../../src/modules/files/public.ts'
void test('small files have no count cap; removing an entry rejects late success', async () => {
  const released: string[] = []
  let complete: ((file: ManagedFile) => void) | undefined
  const queue = createChatFileQueue({
    upload: () =>
      new Promise((resolve) => {
        complete = resolve
      }),
    createObjectURL: () => 'blob:test',
    revokeObjectURL: (url) => released.push(url)
  })
  const files = Array.from({ length: 16 }, (_, index) => new File(['hello'], `${index}.txt`))
  const first = queue.add(files, false)
  assert.equal(first.rejected.length, 0)
  assert.equal(queue.items.length, 16)
  const pending = queue.items.at(-1)!
  queue.remove(pending.id)
  complete!({ fileId: 'late', originalName: 'late.txt' } as ManagedFile)
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.equal(queue.items.length, 15)
  assert.equal(
    queue.items.some((item) => item.fileId === 'late'),
    false
  )
  queue.clear()
  assert.equal(queue.items.length, 0)
  assert.deepEqual(released, [])
})
void test('the total size includes pending and failed uploads and removal frees capacity', async () => {
  const sizedFile = (name: string, size: number): File => {
    const file = new File(['data'], name)
    Object.defineProperty(file, 'size', { value: size })
    return file
  }
  const mib = 1024 * 1024
  let rejectUpload: ((reason: Error) => void) | undefined
  const queue = createChatFileQueue({
    upload: () =>
      new Promise((_resolve, reject) => {
        rejectUpload = reject
      })
  })
  const batch = queue.add(
    [sizedFile('a.txt', 20 * mib), sizedFile('b.txt', 20 * mib), sizedFile('c.txt', 10 * mib)],
    false
  )
  assert.equal(batch.rejected.length, 0)
  assert.equal(
    queue.items.reduce((total, file) => total + file.sizeBytes, 0),
    CHAT_ATTACHMENTS_MAX_BYTES
  )
  assert.match(queue.add([sizedFile('over.txt', 1)], false).rejected[0]!.error, /50MB/)
  rejectUpload!(new Error('upload failed'))
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.equal(queue.items.at(-1)!.status, 'failed')
  assert.match(queue.add([sizedFile('still-over.txt', 1)], false).rejected[0]!.error, /50MB/)
  queue.remove(queue.items.at(-1)!.id)
  assert.equal(queue.add([sizedFile('replacement.txt', 10 * mib)], false).rejected.length, 0)
  assert.match(
    queue.add([sizedFile('single-too-large.txt', 21 * mib)], false).rejected[0]!.error,
    /20MiB/
  )
  queue.clear()
})

void test('image uploads require model vision; supported raster signatures control previews', async () => {
  let uploaded = 0
  const queue = createChatFileQueue({
    upload: () => {
      uploaded++
      return Promise.resolve({ fileId: 'image-1', originalName: 'a.png' } as ManagedFile)
    },
    createObjectURL: () => 'blob:test',
    revokeObjectURL: () => undefined
  })
  assert.equal(queue.add([new File(['fake'], 'a.png')], false).rejected.length, 1)
  assert.equal(uploaded, 0)
  await assert.rejects(() => imagePreviewBlob(new Blob(['<svg></svg>'], { type: 'image/png' })))
  const preview = await imagePreviewBlob(
    new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])], {
      type: 'application/octet-stream'
    })
  )
  assert.equal(preview.type, 'image/png')
})
