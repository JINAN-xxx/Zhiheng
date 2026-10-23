import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createChatFileQueue, imagePreviewBlob } from '../../src/modules/chat/attachments.ts'
import type { ManagedFile } from '../../src/modules/files/public.ts'
void test('pending slots count toward upload limits; removing an entry rejects late success', async () => {
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
  const files = Array.from({ length: 6 }, (_, index) => new File(['hello'], `${index}.txt`))
  const first = queue.add(files, false)
  assert.equal(first.rejected.length, 1)
  assert.equal(queue.items.length, 5)
  const pending = queue.items.at(-1)!
  queue.remove(pending.id)
  complete!({ fileId: 'late', originalName: 'late.txt' } as ManagedFile)
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.equal(queue.items.length, 4)
  assert.equal(
    queue.items.some((item) => item.fileId === 'late'),
    false
  )
  queue.clear()
  assert.equal(queue.items.length, 0)
  assert.deepEqual(released, [])
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
