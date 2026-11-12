import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
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
  stageUncertain: boolean
  error: string
}

interface UploadContext {
  baseId: Readonly<Ref<string>>
  selected: Readonly<Ref<KnowledgeBase | null>>
  folderId: Readonly<Ref<string | null>>
  actionBusy: Readonly<Ref<boolean>>
  uncertain: Ref<boolean>
  onAccepted: (job: KnowledgeJob, destinationFolder: string | null) => Promise<void>
  onDenied?: (error: ApiError) => void
}
// Keep only recovery metadata if browser storage fails during an authentication redirect.
const volatileDrafts = new Map<string, UploadDraft>()
export function useKnowledgeUpload({
  baseId,
  selected,
  folderId,
  actionBusy,
  uncertain,
  onAccepted,
  onDenied
}: UploadContext) {
  const uploadOpen = ref(false),
    uploading = ref(false),
    uploadDiscovering = ref(false),
    uploadDiscarding = ref(false),
    uploadNotice = ref(''),
    uploadError = ref(''),
    uploadFolderId = ref<string | null>(null),
    uploadItems = ref<UploadItem[]>([])
  const uploadFolderLocked = computed(
    () =>
      uploading.value ||
      uploadDiscovering.value ||
      uploadDiscarding.value ||
      uncertain.value ||
      uploadItems.value.length > 0
  )
  const uploadChunks = reactive({ size: 1000 as number | null, overlap: 200 as number | null })
  const writeScope = new RequestScope()
  let uploadKey = '',
    storageKey = '',
    awaitingAdmission = false,
    closeOperation: Promise<boolean> | null = null
  function saveDraft(): boolean {
    const items: StagedUpload[] = []
    for (const item of uploadItems.value)
      if (item.fileId || item.stageUncertain || item.status === 'uploading')
        items.push({ id: item.id, name: item.name, fileId: item.fileId, uploadId: item.uploadId })
    if (!items.length) {
      if (storageKey) clearDraft()
      return true
    }
    if (!storageKey) return false
    const draft: UploadDraft = {
      version: 1,
      requestId: uploadKey,
      folderId: uploadFolderId.value,
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
      if (draft.folderId !== uploadFolderId.value) return
      uploadKey = draft.requestId
      uploadChunks.size = draft.size
      uploadChunks.overlap = draft.overlap
      uncertain.value = awaitingAdmission = draft.uncertain
      uploadDiscarding.value = draft.discarding
      uploadItems.value = draft.items.map((item) => ({
        ...item,
        file: null,
        status: item.fileId ? 'uploaded' : 'queued',
        stageUncertain: !item.fileId,
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
  async function openUpload(targetFolder: string | null = folderId.value): Promise<void> {
    if (
      actionBusy.value ||
      uploading.value ||
      uploadDiscovering.value ||
      !!selected.value?.active_job_id ||
      !selected.value?.can_manage
    )
      return
    if (uploadItems.value.length) {
      uploadOpen.value = true
      return
    }
    uploadError.value = ''
    uploadItems.value = []
    uploadKey = createRequestId()
    uploadFolderId.value = targetFolder
    storageKey = `zhiheng:knowledge-upload:${identity.value?.id ?? ''}:${baseId.value}:${targetFolder ?? 'root'}`
    awaitingAdmission = false
    uploadDiscarding.value = false
    uploadNotice.value = ''
    uploadChunks.size = selected.value.chunk_size
    uploadChunks.overlap = selected.value.chunk_overlap
    uploadOpen.value = true
    uncertain.value = false
    restoreDraft()
    if (uploadItems.value.length) return
    const ticket = writeScope.replace(),
      id = baseId.value
    uploadDiscovering.value = true
    try {
      const pending = await api.getPendingUploads(id, targetFolder, ticket.signal)
      if (!ticket.isCurrent() || !uploadOpen.value) return
      uploadItems.value = pending.items.map((item) => ({
        id: item.requestId,
        name: item.file.originalName,
        file: null,
        fileId: item.file.fileId,
        uploadId: item.uploadId,
        status: 'uploaded' as const,
        stageUncertain: false,
        error: ''
      }))
      if (pending.total) {
        uploadNotice.value = `发现 ${pending.total} 个待导入原文，已恢复本批 ${pending.items.length} 个，可继续导入或取消清理。暂存保留 24 小时。`
        saveDraft()
      }
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) uploadError.value = errorMessage(reason)
    } finally {
      if (ticket.isCurrent()) uploadDiscovering.value = false
    }
  }
  async function selectUploadFolder(id: string | null): Promise<void> {
    if (!uploadOpen.value || uploadFolderLocked.value || id === uploadFolderId.value) return
    await openUpload(id)
  }
  function queueFile(file: File): boolean {
    if (uploading.value || uploadDiscovering.value || uncertain.value || uploadDiscarding.value)
      return false
    if (
      uploadItems.value.length >= 20 &&
      !uploadItems.value.some((item) => !item.file && !item.fileId && item.name === file.name)
    ) {
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
    const retained = uploadItems.value.find(
      (item) => !item.file && !item.fileId && item.name === file.name
    )
    if (retained) {
      retained.file = file
      retained.error = ''
      saveDraft()
      return false
    }
    uploadItems.value.push({
      id: createRequestId(),
      file,
      name: file.name,
      fileId: null,
      uploadId: null,
      status: 'queued',
      stageUncertain: false,
      error: ''
    })
    saveDraft()
    return false
  }
  async function submitUpload(): Promise<void> {
    if (
      uploading.value ||
      uploadDiscovering.value ||
      uploadDiscarding.value ||
      !uploadItems.value.length
    )
      return
    if (!validChunks(uploadChunks.size, uploadChunks.overlap)) {
      uploadError.value = '请填写有效的切片大小与重叠大小'
      return
    }
    const ticket = writeScope.replace(),
      id = baseId.value,
      destinationFolder = uploadFolderId.value
    uploading.value = true
    uploadError.value = ''
    try {
      for (const item of uploadItems.value) {
        if (item.fileId) continue
        if (!item.file) {
          try {
            const receipt = await api.getUploadReceipt(id, item.id, ticket.signal)
            if (!ticket.isCurrent()) return
            item.fileId = receipt.file.fileId
            item.uploadId = receipt.upload_id
            item.status = 'uploaded'
            item.stageUncertain = false
            saveDraft()
            continue
          } catch (reason) {
            if (reason instanceof ApiError && reason.code === 'knowledge_upload_expired') {
              item.id = createRequestId()
              item.stageUncertain = false
              saveDraft()
              throw new Error('上次上传已过期，请重新选择原文件')
            }
            if (!(reason instanceof ApiError) || reason.status !== 404) throw reason
            throw new Error('请重新选择未接纳的原文件；将沿用原上传标识')
          }
        }
        item.status = 'uploading'
        item.error = ''
        saveDraft()
        try {
          const result = await api.uploadDocument(
            id,
            item.file,
            destinationFolder,
            ticket.signal,
            item.id
          )
          if (!ticket.isCurrent()) return
          item.fileId = result.file.fileId
          item.uploadId = result.upload_id
          item.status = 'uploaded'
          item.stageUncertain = false
          saveDraft()
        } catch (error) {
          if (!ticket.isCurrent() || isAborted(error)) return
          item.status = 'failed'
          item.stageUncertain = !(error instanceof ApiError) || error.status >= 500
          if (error instanceof ApiError && error.code === 'knowledge_upload_expired') {
            item.id = createRequestId()
            item.file = null
          }
          item.error = errorMessage(error)
          saveDraft()
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
          folder_id: destinationFolder,
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
      await onAccepted(job, destinationFolder)
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        if (error instanceof ApiError && [401, 403, 404].includes(error.status) && onDenied) {
          onDenied(error)
          return
        }
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
    uploadDiscovering.value = false
    uploading.value = true
    uploadDiscarding.value = true
    saveDraft()
    const operation = (async () => {
      try {
        for (const item of uploadItems.value) {
          if (!item.uploadId && !item.fileId && item.stageUncertain) {
            try {
              const receipt = await api.getUploadReceipt(id, item.id, ticket.signal)
              if (!ticket.isCurrent()) return false
              item.uploadId = receipt.upload_id
            } catch (reason) {
              if (
                !(reason instanceof ApiError) ||
                (reason.status !== 404 && reason.code !== 'knowledge_upload_expired')
              )
                throw reason
            }
          }
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
    uploadDiscovering.value = false
    uploading.value = false
    uploadItems.value = []
    uploadError.value = ''
    uploadNotice.value = ''
    uploadFolderId.value = null
    uploadDiscarding.value = false
    awaitingAdmission = false
    storageKey = ''
  }
  function retainAndResetUpload(): void {
    saveDraft()
    resetUpload()
    uncertain.value = false
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
    uploadDiscovering,
    uploading,
    uploadDiscarding,
    uploadNotice,
    uploadError,
    uploadItems,
    uploadFolderId,
    uploadFolderLocked,
    uploadChunks,
    openUpload,
    selectUploadFolder,
    closeUpload,
    queueFile,
    submitUpload,
    resetUpload,
    retainAndResetUpload
  }
}
