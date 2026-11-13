import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { emptyQuery, listFiles } from '../../files/public.ts'
import type { ManagedFile } from '../../files/public.ts'
import { importDocuments } from '../api/index.ts'
import type { KnowledgeBase, KnowledgeDocument, KnowledgeJob } from '../api/index.ts'
import { validChunks } from '../presentation.ts'
import { managedImportStorageKey, parseManagedImportDraft } from '../managedImportDraft.ts'
import type { ManagedImportDraft } from '../managedImportDraft.ts'

interface ManagedImportContext {
  baseId: Readonly<Ref<string>>
  userId: Readonly<Ref<string>>
  selected: Readonly<Ref<KnowledgeBase | null>>
  documents: Readonly<Ref<KnowledgeDocument[]>>
  folderId: Readonly<Ref<string | null>>
  actionBusy: Readonly<Ref<boolean>>
  uploading: Readonly<Ref<boolean>>
  busy: Readonly<Ref<boolean>>
  onAccepted: (job: KnowledgeJob) => Promise<void>
}

export function useManagedImport({
  baseId,
  userId,
  selected,
  documents,
  folderId,
  actionBusy,
  uploading,
  busy,
  onAccepted: acceptJob
}: ManagedImportContext) {
  const importScope = new RequestScope()
  const emptyManagedImport = () => ({
    open: false,
    loading: false,
    saving: false,
    uncertain: false,
    recoveryBlocked: false,
    names: [] as { id: string; name: string }[],
    error: '',
    search: '',
    files: [] as ManagedFile[],
    ids: [] as string[],
    size: 1000 as number | null,
    overlap: 200 as number | null,
    key: '',
    page: 1,
    total: 0,
    folder: null as string | null
  })
  const managedImport = reactive(emptyManagedImport())
  let pendingImport: ManagedImportDraft | null = null
  const importDraftVersion = ref(0)
  const canManage = computed(() =>
    Boolean(
      userId.value &&
        baseId.value &&
        selected.value?.id === baseId.value &&
        selected.value.can_manage
    )
  )
  const permissionDenied = (reason: unknown) =>
    reason instanceof ApiError && [401, 403, 404].includes(reason.status)
  function resetManagedImport(): void {
    importScope.replace()
    pendingImport = null
    Object.assign(managedImport, emptyManagedImport())
  }
  function closeManagedImport(): void {
    if (!managedImport.saving && !managedImport.uncertain) resetManagedImport()
  }
  // Revoke the whole in-memory context, but leave admission credentials in session storage.
  watch(
    [baseId, userId, () => selected.value?.id, () => selected.value?.can_manage],
    resetManagedImport,
    { flush: 'sync' }
  )
  onScopeDispose(() => {
    resetManagedImport()
    importScope.dispose()
  })
  const importRecoverable = computed(() => {
    void importDraftVersion.value
    if (!canManage.value || actionBusy.value || uploading.value) return false
    try {
      return sessionStorage.getItem(managedImportStorageKey(userId.value, baseId.value)) !== null
    } catch {
      return false
    }
  })
  function clearManagedImportDraft(draft: ManagedImportDraft): void {
    try {
      const key = managedImportStorageKey(draft.userId, draft.baseId)
      const raw = sessionStorage.getItem(key)
      if (
        raw &&
        parseManagedImportDraft(JSON.parse(raw), draft.userId, draft.baseId).request
          .client_request_id === draft.request.client_request_id
      )
        sessionStorage.removeItem(key)
    } catch {
      /* Retaining the original identity is safe if optional storage cannot be cleared. */
    }
    if (
      pendingImport?.request.client_request_id === draft.request.client_request_id &&
      pendingImport.userId === draft.userId &&
      pendingImport.baseId === draft.baseId
    )
      pendingImport = null
    importDraftVersion.value += 1
  }
  function searchImportFiles(): void {
    managedImport.page = 1
    void loadImportFiles()
  }
  async function loadImportFiles(): Promise<void> {
    if (
      !managedImport.open ||
      !canManage.value ||
      managedImport.saving ||
      managedImport.uncertain ||
      managedImport.recoveryBlocked
    )
      return
    const ticket = importScope.replace()
    managedImport.loading = true
    managedImport.error = ''
    try {
      const page = await listFiles(
        { ...emptyQuery(), originalName: managedImport.search, pageNum: managedImport.page },
        ticket.signal
      )
      if (ticket.isCurrent()) {
        managedImport.files = page.rows
        managedImport.total = page.total
      }
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        if (permissionDenied(reason)) resetManagedImport()
        else managedImport.error = errorMessage(reason)
      }
    } finally {
      if (ticket.isCurrent()) managedImport.loading = false
    }
  }
  function openManagedImport(): void {
    const currentUserId = userId.value,
      id = baseId.value
    if (
      !currentUserId ||
      !canManage.value ||
      managedImport.saving ||
      actionBusy.value ||
      uploading.value ||
      (busy.value && !importRecoverable.value)
    )
      return
    resetManagedImport()
    Object.assign(managedImport, {
      open: true,
      size: selected.value!.chunk_size,
      overlap: selected.value!.chunk_overlap,
      folder: folderId.value
    })
    try {
      const raw = sessionStorage.getItem(managedImportStorageKey(currentUserId, id))
      if (raw !== null) {
        const draft = parseManagedImportDraft(JSON.parse(raw), currentUserId, id)
        pendingImport = draft
        Object.assign(managedImport, {
          uncertain: true,
          ids: [...draft.request.file_ids],
          names: draft.files,
          size: draft.request.chunk_size,
          overlap: draft.request.chunk_overlap,
          key: draft.request.client_request_id,
          folder: draft.request.folder_id
        })
        return
      }
    } catch {
      managedImport.recoveryBlocked = true
      managedImport.error = '无法读取导入恢复信息，请刷新核对已有索引任务后再操作。'
      return
    }
    managedImport.key = createRequestId()
    void loadImportFiles()
  }

  function canImport(file: ManagedFile): boolean {
    return (
      !file.sourceFileId &&
      file.status === 'active' &&
      file.storageStatus === 'normal' &&
      (!file.expireTime || Date.parse(file.expireTime) > Date.now()) &&
      !documents.value.some((doc) => doc.file_id === file.fileId)
    )
  }
  function toggleImport(id: string, checked: boolean): void {
    if (
      !managedImport.open ||
      !canManage.value ||
      managedImport.saving ||
      managedImport.uncertain ||
      managedImport.recoveryBlocked
    )
      return
    if (checked && managedImport.ids.length >= 20) {
      managedImport.error = '每批最多导入 20 个文档'
      return
    }
    managedImport.ids = checked
      ? [...new Set([...managedImport.ids, id])]
      : managedImport.ids.filter((value) => value !== id)
    managedImport.names = managedImport.ids.map((fileId) => ({
      id: fileId,
      name:
        managedImport.names.find((file) => file.id === fileId)?.name ??
        managedImport.files.find((file) => file.fileId === fileId)?.displayName ??
        fileId
    }))
  }
  async function submitManagedImport(): Promise<void> {
    const currentUserId = userId.value,
      id = baseId.value
    if (
      managedImport.saving ||
      managedImport.recoveryBlocked ||
      !managedImport.open ||
      !canManage.value ||
      !currentUserId
    )
      return
    if (!managedImport.ids.length || !validChunks(managedImport.size, managedImport.overlap)) {
      managedImport.error = '请选择文件并填写有效的切片大小与重叠大小'
      return
    }
    const draft: ManagedImportDraft = pendingImport ?? {
      version: 1,
      userId: currentUserId,
      baseId: id,
      request: {
        file_ids: [...managedImport.ids],
        folder_id: managedImport.folder,
        chunk_size: managedImport.size!,
        chunk_overlap: managedImport.overlap!,
        client_request_id: managedImport.key
      },
      files: managedImport.names.map((file) => ({ ...file }))
    }
    if (draft.userId !== currentUserId || draft.baseId !== id) {
      managedImport.error = '当前用户或知识库已变化，请重新打开导入。'
      return
    }
    try {
      sessionStorage.setItem(managedImportStorageKey(currentUserId, id), JSON.stringify(draft))
    } catch {
      managedImport.error = '无法保留导入请求，请恢复浏览器会话存储后重试；本次尚未提交。'
      return
    }
    pendingImport = draft
    importDraftVersion.value += 1
    const ticket = importScope.replace()
    managedImport.saving = true
    managedImport.uncertain = true
    managedImport.error = ''
    try {
      const job = await importDocuments(
        id,
        { ...draft.request, file_ids: [...draft.request.file_ids] },
        ticket.signal
      )
      if (!ticket.isCurrent()) return
      clearManagedImportDraft(draft)
      resetManagedImport()
      await acceptJob(job)
    } catch (reason) {
      if (!ticket.isCurrent() || isAborted(reason)) return
      if (permissionDenied(reason)) {
        resetManagedImport()
        return
      }
      if (reason instanceof ApiError && reason.status < 500) {
        clearManagedImportDraft(draft)
        if (ticket.isCurrent()) {
          managedImport.uncertain = false
          managedImport.key = createRequestId()
        }
      }
      if (ticket.isCurrent() && !isAborted(reason)) managedImport.error = errorMessage(reason)
    } finally {
      if (ticket.isCurrent()) managedImport.saving = false
    }
  }

  return {
    managedImport,
    importRecoverable,
    searchImportFiles,
    loadImportFiles,
    openManagedImport,
    closeManagedImport,
    canImport,
    toggleImport,
    submitManagedImport
  }
}
