import { onBeforeUnmount, reactive, ref } from 'vue'
import type { Ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity } from '../../identity/public.ts'
import * as api from '../api/index.ts'
import type { KnowledgeBase, KnowledgeJob } from '../api/index.ts'
import { validChunks } from '../presentation.ts'
import { parseUploadDraft } from '../uploadDraft.ts'
import type { StagedUpload, UploadDraft } from '../uploadDraft.ts'

interface UploadItem {
  id: string
  file: File | null
  name: string
  fileId: string | null
  uploadId: string | null
  status: 'queued' | 'uploading' | 'uploaded' | 'failed'
  error: string
}

interface UploadContext {
  baseId: Readonly<Ref<string>>
  selected: Readonly<Ref<KnowledgeBase | null>>
  folderId: Readonly<Ref<string | null>>
  actionBusy: Readonly<Ref<boolean>>
  uncertain: Ref<boolean>
  onAccepted: (job: KnowledgeJob) => Promise<void>
}
// Keep only recovery metadata if browser storage fails during an authentication redirect.
const volatileDrafts = new Map<string, UploadDraft>()
export function useKnowledgeUpload({
  baseId,
  selected,
  folderId,
  actionBusy,
  uncertain,
  onAccepted
}: UploadContext) {
  const uploadOpen = ref(false),
    uploading = ref(false),
    uploadDiscarding = ref(false),
    uploadNotice = ref(''),
    uploadError = ref(''),
    uploadItems = ref<UploadItem[]>([])
  const uploadChunks = reactive({ size: 1000 as number | null, overlap: 200 as number | null })
  const writeScope = new RequestScope()
  let uploadKey = '',
    uploadFolder: string | null = null,
    storageKey = '',
    awaitingAdmission = false,
    closeOperation: Promise<boolean> | null = null
  function saveDraft(): boolean {
    const items: StagedUpload[] = []
    for (const item of uploadItems.value)
      if (item.fileId && item.uploadId)
        items.push({ id: item.id, name: item.name, fileId: item.fileId, uploadId: item.uploadId })
    if (!items.length) return true
    if (!storageKey) return false
    const draft: UploadDraft = {
      version: 1,
      requestId: uploadKey,
      folderId: uploadFolder,
      size: uploadChunks.size,
      overlap: uploadChunks.overlap,
      uncertain: awaitingAdmission,
      discarding: uploadDiscarding.value,
      complete: uploadItems.value.every((item) => !!item.fileId),
      items
    }
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(draft))
      volatileDrafts.delete(storageKey)
      return true
    } catch {
      volatileDrafts.set(storageKey, draft)
      return false
    }
  }
  function clearDraft(): void {
    volatileDrafts.delete(storageKey)
    try {
      if (storageKey) sessionStorage.removeItem(storageKey)
    } catch {
      /* A later recovery still uses the original request or an idempotent discard. */
    }
  }
  function restoreDraft(): void {
    try {
      let draft = volatileDrafts.get(storageKey)
      if (!draft) {
        const raw = sessionStorage.getItem(storageKey)
        if (!raw) return
        draft = parseUploadDraft(JSON.parse(raw))
      }
      if (draft.folderId !== uploadFolder) return
      uploadKey = draft.requestId
      uploadChunks.size = draft.size
      uploadChunks.overlap = draft.overlap
      uncertain.value = awaitingAdmission = draft.uncertain
      uploadDiscarding.value = draft.discarding
      uploadItems.value = draft.items.map((item) => ({
        ...item,
        file: null,
        status: 'uploaded',
        error: ''
      }))
      uploadNotice.value = draft.discarding
        ? '上次取消尚未完成，请再次取消以确认清理。'
        : draft.uncertain
          ? '已恢复上次提交，请确认原提交结果。'
          : draft.complete
            ? '已恢复上次已上传的原文，可继续导入或取消清理。'
            : '已恢复成功上传的原文，其余文件请重新选择；也可取消清理。'
    } catch {
      /* Invalid or unavailable optional storage cannot start a request. */
    }
  }
  function openUpload(): void {
    if (
      actionBusy.value ||
      uploading.value ||
      !!selected.value?.active_job_id ||
      !selected.value?.can_manage
    )
      return
    if (uploadItems.value.some((item) => item.uploadId)) {
      uploadOpen.value = true
      return
    }
    uploadError.value = ''
    uploadItems.value = []
    uploadKey = crypto.randomUUID()
    uploadFolder = folderId.value
    storageKey = `zhiheng:knowledge-upload:${identity.value?.id ?? ''}:${baseId.value}:${uploadFolder ?? 'root'}`
    awaitingAdmission = false
    uploadDiscarding.value = false
    uploadNotice.value = ''
    uploadChunks.size = selected.value.chunk_size
    uploadChunks.overlap = selected.value.chunk_overlap
    uploadOpen.value = true
    uncertain.value = false
    restoreDraft()
  }
  function queueFile(file: File): boolean {
    if (uploading.value || uncertain.value || uploadDiscarding.value) return false
    if (uploadItems.value.length >= 20) {
      uploadError.value = '每批最多选择 20 个文档'
      return false
    }
    if (!file.size || file.size > 20 * 1024 * 1024) {
      uploadError.value = '请选择非空且不超过 20MiB 的文档'
      return false
    }
    const extension = file.name.split('.').at(-1)?.toLowerCase() ?? ''
    if (!['pdf', 'docx', 'xlsx', 'txt', 'md', 'csv', 'json', 'html', 'htm'].includes(extension)) {
      uploadError.value = '该格式暂不支持原生文本索引；扫描件和图片需要 OCR'
      return false
    }
    uploadItems.value.push({
      id: crypto.randomUUID(),
      file,
      name: file.name,
      fileId: null,
      uploadId: null,
      status: 'queued',
      error: ''
    })
    saveDraft()
    return false
  }
  async function submitUpload(): Promise<void> {
    if (uploading.value || uploadDiscarding.value || !uploadItems.value.length) return
    if (!validChunks(uploadChunks.size, uploadChunks.overlap)) {
      uploadError.value = '请填写有效的切片大小与重叠大小'
      return
    }
    const ticket = writeScope.replace(),
      id = baseId.value
    uploading.value = true
    uploadError.value = ''
    try {
      for (const item of uploadItems.value) {
        if (item.fileId) continue
        if (!item.file) throw new Error('请重新选择未上传的文件')
        item.status = 'uploading'
        item.error = ''
        try {
          const result = await api.uploadDocument(id, item.file, uploadFolder, ticket.signal)
          if (!ticket.isCurrent()) return
          item.fileId = result.file.fileId
          item.uploadId = result.upload_id
          item.status = 'uploaded'
          saveDraft()
        } catch (error) {
          if (!ticket.isCurrent() || isAborted(error)) return
          item.status = 'failed'
          item.error = errorMessage(error)
          throw error
        }
      }
      const ids = uploadItems.value.map((item) => item.fileId!)
      awaitingAdmission = true
      saveDraft()
      const job = await api.importDocuments(
        id,
        {
          file_ids: ids,
          folder_id: uploadFolder,
          chunk_size: uploadChunks.size!,
          chunk_overlap: uploadChunks.overlap!,
          client_request_id: uploadKey
        },
        ticket.signal
      )
      if (!ticket.isCurrent()) return
      awaitingAdmission = false
      clearDraft()
      uploadItems.value = []
      uploadOpen.value = false
      uncertain.value = false
      uploading.value = false
      await onAccepted(job)
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        uploadError.value = errorMessage(error)
        uncertain.value =
          uploadItems.value.every((item) => !!item.fileId) &&
          (!(error instanceof ApiError) || error.status >= 500)
        awaitingAdmission = uncertain.value
        saveDraft()
      }
    } finally {
      if (ticket.isCurrent()) uploading.value = false
    }
  }
  function closeUpload(): Promise<boolean> {
    if (closeOperation) return closeOperation
    if (uploading.value || uncertain.value) return Promise.resolve(false)
    const ticket = writeScope.replace(),
      id = baseId.value
    uploading.value = true
    uploadDiscarding.value = true
    saveDraft()
    const operation = (async () => {
      try {
        for (const item of uploadItems.value) {
          if (!item.uploadId) continue
          try {
            await api.discardUpload(id, item.uploadId, ticket.signal)
          } catch (error) {
            if (!(error instanceof ApiError) || error.status !== 404) throw error
          }
          if (!ticket.isCurrent()) return false
          item.uploadId = null
          saveDraft()
        }
        clearDraft()
        resetUpload()
        return true
      } catch (error) {
        if (ticket.isCurrent() && !isAborted(error)) {
          uploadError.value = `${errorMessage(error)}；请再次取消以确认暂存清理。`
          uploadOpen.value = true
          saveDraft()
        }
        return false
      } finally {
        if (ticket.isCurrent()) uploading.value = false
      }
    })()
    closeOperation = operation
    void operation.then(() => {
      if (closeOperation === operation) closeOperation = null
    })
    return operation
  }
  function resetUpload(): void {
    writeScope.replace()
    uploadOpen.value = false
    uploading.value = false
    uploadItems.value = []
    uploadError.value = ''
    uploadNotice.value = ''
    uploadDiscarding.value = false
    awaitingAdmission = false
    storageKey = ''
  }
  function retainBeforeLeaving(): boolean {
    if (identity.value && uploading.value && uploadItems.value.some((item) => !item.fileId)) {
      uploadOpen.value = true
      uploadError.value = '原文仍在上传，请等待上传完成后再离开。'
      return false
    }
    if (saveDraft() || !identity.value) return true
    uploadOpen.value = true
    uploadError.value = '无法保留未完成的上传，请先完成或取消此批次再离开。'
    return false
  }
  onBeforeRouteLeave(retainBeforeLeaving)
  onBeforeRouteUpdate((to, from) => to.path === from.path || retainBeforeLeaving())
  onBeforeUnmount(() => {
    saveDraft()
    writeScope.dispose()
  })
  return {
    uploadOpen,
    uploading,
    uploadDiscarding,
    uploadNotice,
    uploadError,
    uploadItems,
    uploadChunks,
    openUpload,
    closeUpload,
    queueFile,
    submitUpload,
    resetUpload
  }
}
