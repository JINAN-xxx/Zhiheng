import { validChunks } from './presentation.ts'

export interface ManagedImportRequest {
  file_ids: string[]
  folder_id: string | null
  chunk_size: number
  chunk_overlap: number
  client_request_id: string
}
export interface ManagedImportDraft {
  version: 1
  userId: string
  baseId: string
  request: ManagedImportRequest
  files: { id: string; name: string }[]
}
export const managedImportStorageKey = (userId: string, baseId: string): string =>
  `zhiheng:knowledge-import:${encodeURIComponent(userId)}:${encodeURIComponent(baseId)}`
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('导入恢复信息无效')
  return value as Record<string, unknown>
}
function nonempty(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('导入恢复信息无效')
  return value
}
export function parseManagedImportDraft(
  value: unknown,
  userId: string,
  baseId: string
): ManagedImportDraft {
  const draft = record(value),
    request = record(draft.request)
  if (
    draft.version !== 1 ||
    draft.userId !== userId ||
    draft.baseId !== baseId ||
    !Array.isArray(request.file_ids) ||
    !Array.isArray(draft.files)
  )
    throw new Error('导入恢复信息无效')
  const ids = request.file_ids.map(nonempty)
  if (
    !ids.length ||
    ids.length > 20 ||
    new Set(ids).size !== ids.length ||
    (request.folder_id !== null && typeof request.folder_id !== 'string') ||
    typeof request.chunk_size !== 'number' ||
    typeof request.chunk_overlap !== 'number' ||
    !validChunks(request.chunk_size, request.chunk_overlap)
  )
    throw new Error('导入恢复信息无效')
  const files = draft.files.map((value) => {
    const file = record(value)
    return { id: nonempty(file.id), name: nonempty(file.name) }
  })
  if (files.length !== ids.length || files.some((file, index) => file.id !== ids[index]))
    throw new Error('导入恢复信息无效')
  return {
    version: 1,
    userId,
    baseId,
    request: {
      file_ids: ids,
      folder_id: request.folder_id,
      chunk_size: request.chunk_size,
      chunk_overlap: request.chunk_overlap,
      client_request_id: nonempty(request.client_request_id)
    },
    files
  }
}
