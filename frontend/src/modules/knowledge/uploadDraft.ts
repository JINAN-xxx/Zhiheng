import { boolean, list, nullableNumber, number, object, string } from '../../shared/validation.ts'

export interface StagedUpload {
  id: string
  name: string
  fileId: string | null
  uploadId: string | null
}
export interface UploadDraft {
  version: 1
  requestId: string
  folderId: string | null
  size: number | null
  overlap: number | null
  uncertain: boolean
  discarding: boolean
  complete: boolean
  items: StagedUpload[]
}

export function parseUploadDraft(value: unknown): UploadDraft {
  const data = object(value)
  if (number(data.version) !== 1) throw new Error('Unsupported upload draft')
  const items = list((value): StagedUpload => {
    const item = object(value)
    return {
      id: string(item.id),
      name: string(item.name),
      fileId: item.fileId === null ? null : string(item.fileId),
      uploadId: item.uploadId === null ? null : string(item.uploadId)
    }
  })(data.items)
  if (!items.length || items.length > 20) throw new Error('Invalid upload draft size')
  return {
    version: 1,
    requestId: string(data.requestId),
    folderId: data.folderId === null ? null : string(data.folderId),
    size: nullableNumber(data.size),
    overlap: nullableNumber(data.overlap),
    uncertain: boolean(data.uncertain),
    discarding: boolean(data.discarding),
    complete: boolean(data.complete),
    items
  }
}
