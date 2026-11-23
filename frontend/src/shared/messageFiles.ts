import { imagePreviewBlob } from './attachmentQueue.ts'

export type FilePreviewKind = 'image' | 'pdf' | 'markdown' | 'text' | ''
export interface FilePreview {
  kind: FilePreviewKind
  blob: Blob
}
export interface MessageFile {
  id: string
  name: string
  mime: string
  size: number
  image: boolean
  detail?: string
}
export type ReadMessageFile = (id: string, signal: AbortSignal) => Promise<Blob>
export type ReadFilePreview = (id: string, signal: AbortSignal) => Promise<FilePreview | null>

export function attachmentFile(file: {
  file_id: string
  original_filename: string
  mime: string
  size: number
}): MessageFile {
  return {
    id: file.file_id,
    name: file.original_filename,
    mime: file.mime,
    size: file.size,
    image: /^image\/(png|jpeg|gif|webp)$/i.test(file.mime)
  }
}

export async function attachmentPreview(
  file: MessageFile,
  read: ReadMessageFile,
  signal: AbortSignal
): Promise<FilePreview | null> {
  const extension = file.name.split('.').at(-1)?.toLowerCase() ?? ''
  if (file.image) return makeFilePreview('image', await read(file.id, signal))
  if (file.mime === 'application/pdf' || extension === 'pdf') {
    const blob = await read(file.id, signal)
    return makeFilePreview('pdf', blob)
  }
  if (['txt', 'md', 'csv', 'json', 'html', 'htm'].includes(extension))
    return { kind: 'text', blob: await read(file.id, signal) }
  return null
}

export async function makeFilePreview(
  kind: FilePreviewKind,
  blob: Blob
): Promise<FilePreview | null> {
  if (kind === 'image') return { kind, blob: await imagePreviewBlob(blob) }
  if (kind === 'pdf') {
    if ((await blob.slice(0, 5).text()) !== '%PDF-') throw new Error('该文件无法作为 PDF 预览')
    return { kind, blob: new Blob([blob], { type: 'application/pdf' }) }
  }
  return kind ? { kind, blob } : null
}

export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(2)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob),
    anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
