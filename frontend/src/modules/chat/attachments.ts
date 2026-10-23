import type { ManagedFile } from '../files/public.ts'
import { errorMessage } from '../../shared/http.ts'
const IMAGES = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp'])
const DOCUMENTS = new Set(['pdf', 'docx', 'xlsx', 'txt', 'md', 'csv', 'json', 'html', 'htm'])
export const CHAT_FILE_ACCEPT =
  '.png,.jpg,.jpeg,.gif,.webp,.pdf,.docx,.xlsx,.txt,.md,.csv,.json,.html,.htm'
export interface QueuedFile {
  id: string
  kind: 'image' | 'attachment'
  fileName: string
  sizeBytes: number
  fileId: string | null
  status: 'uploading' | 'ready' | 'failed'
  error: string
  previewUrl: string
}
interface Entry extends QueuedFile {
  file: File
  controller: AbortController
}
export async function imagePreviewBlob(blob: Blob): Promise<Blob> {
  const bytes = new Uint8Array(await blob.slice(0, 12).arrayBuffer()),
    text = String.fromCharCode(...bytes)
  const starts = (signature: number[]) => signature.every((value, index) => bytes[index] === value)
  const mime = starts([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
    ? 'image/png'
    : starts([0xff, 0xd8, 0xff])
      ? 'image/jpeg'
      : text.startsWith('GIF87a') || text.startsWith('GIF89a')
        ? 'image/gif'
        : text.startsWith('RIFF') && text.slice(8, 12) === 'WEBP'
          ? 'image/webp'
          : ''
  if (!mime) throw new Error('该文件无法作为图片预览')
  return new Blob([blob], { type: mime })
}
export function createChatFileQueue(options: {
  upload: (file: File, signal: AbortSignal) => Promise<ManagedFile>
  onChange?: (files: QueuedFile[]) => void
  createObjectURL?: (file: Blob) => string
  revokeObjectURL?: (url: string) => void
}) {
  let entries: Entry[] = [],
    sequence = 0
  const snapshots = (): QueuedFile[] =>
    entries.map(({ id, kind, fileName, sizeBytes, fileId, status, error, previewUrl }) => ({
      id,
      kind,
      fileName,
      sizeBytes,
      fileId,
      status,
      error,
      previewUrl
    }))
  const publish = () => options.onChange?.(snapshots())
  const create = options.createObjectURL ?? ((file: Blob) => URL.createObjectURL(file)),
    revoke = options.revokeObjectURL ?? ((url: string) => URL.revokeObjectURL(url))
  const current = (entry: Entry, controller: AbortController) =>
    entries.includes(entry) && entry.controller === controller && !controller.signal.aborted
  async function start(entry: Entry): Promise<void> {
    entry.controller.abort()
    const controller = new AbortController()
    entry.controller = controller
    entry.status = 'uploading'
    entry.error = ''
    publish()
    try {
      const file = await options.upload(entry.file, controller.signal)
      if (!current(entry, controller)) return
      entry.fileId = file.fileId
      entry.fileName = file.originalName
      entry.status = 'ready'
      if (entry.kind === 'image') {
        try {
          const blob = await imagePreviewBlob(entry.file)
          if (current(entry, controller)) entry.previewUrl = create(blob)
        } catch {
          /* Server validates actual content before admission. */
        }
      }
    } catch (reason) {
      if (!current(entry, controller)) return
      entry.status = 'failed'
      entry.error = `${errorMessage(reason)}；重新上传前请核对文件列表，可能已经上传成功。`
    } finally {
      if (current(entry, controller)) publish()
    }
  }
  function release(entry: Entry): void {
    entry.controller.abort()
    if (entry.previewUrl) revoke(entry.previewUrl)
  }
  return {
    get items(): QueuedFile[] {
      return snapshots()
    },
    add(
      files: Iterable<File>,
      vision: boolean
    ): { rejected: { fileName: string; error: string }[]; uploads: Promise<void[]> } {
      const accepted: Entry[] = [],
        rejected: { fileName: string; error: string }[] = []
      for (const file of files) {
        const extension = file.name.split('.').pop()?.toLowerCase() ?? '',
          kind = IMAGES.has(extension) ? 'image' : DOCUMENTS.has(extension) ? 'attachment' : null
        const limit = kind === 'image' ? 10 : 5
        const error = !kind
          ? '不支持此文件类型'
          : kind === 'image' && !vision
            ? '请选择已启用视觉能力的模型后添加图片'
            : !file.size
              ? '不能上传空文件'
              : file.size > 20 * 1024 * 1024
                ? '单个文件不能超过20MiB'
                : entries.filter((item) => item.kind === kind).length >= limit
                  ? `每条消息最多添加${limit}${kind === 'image' ? '张图片' : '个文档'}`
                  : ''
        if (error || !kind) {
          rejected.push({ fileName: file.name, error })
          continue
        }
        const entry: Entry = {
          id: `chat-file-${++sequence}`,
          file,
          kind,
          fileName: file.name,
          sizeBytes: file.size,
          fileId: null,
          status: 'uploading',
          error: '',
          previewUrl: '',
          controller: new AbortController()
        }
        entries.push(entry)
        accepted.push(entry)
      }
      publish()
      return { rejected, uploads: Promise.all(accepted.map(start)) }
    },
    retry(id: string): Promise<void> {
      const entry = entries.find((item) => item.id === id)
      return entry?.status === 'failed' ? start(entry) : Promise.resolve()
    },
    remove(id: string): void {
      const entry = entries.find((item) => item.id === id)
      if (!entry) return
      entries = entries.filter((item) => item !== entry)
      release(entry)
      publish()
    },
    clear(): void {
      const old = entries
      entries = []
      old.forEach(release)
      publish()
    }
  }
}
