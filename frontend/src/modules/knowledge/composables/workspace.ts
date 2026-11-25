import { canUseCapability, canUseModule } from '../../identity/public.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  isDefiniteRejection,
  ApiError,
  errorMessage,
  isAborted,
  RequestScope
} from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { waitForBackgroundObservation } from '../../../shared/backgroundObservation.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import * as api from '../api/index.ts'
import type {
  KnowledgeBase,
  KnowledgeBaseListItem,
  KnowledgeDocument,
  KnowledgeFolder,
  KnowledgeHit,
  KnowledgeJob
} from '../api/index.ts'
import { validChunks } from '../presentation.ts'
import { useKnowledgeQuery } from './useKnowledgeQuery.ts'
import { useKnowledgeUpload } from './useKnowledgeUpload.ts'
import { useKnowledgeGraph } from './useKnowledgeGraph.ts'
import { jobFinished, useKnowledgeJobObservation } from './useKnowledgeJobObservation.ts'
import { useKnowledgeLocation } from './useKnowledgeLocation.ts'

type EditorKind = 'create' | 'base' | 'createFolder' | 'folder' | 'rechunk'

export function useKnowledgeWorkspace(onJobCompleted?: (job: KnowledgeJob) => void) {
  const bases = ref<KnowledgeBaseListItem[]>([]),
    selected = ref<KnowledgeBase | null>(null)
  const folders = ref<KnowledgeFolder[]>([]),
    documents = ref<KnowledgeDocument[]>([]),
    jobs = ref<KnowledgeJob[]>([])
  const {
    baseId,
    view,
    folderId,
    folderQuery,
    folderFromRoute,
    synchronizeFolderLocation,
    selectFolder,
    navigate,
    back
  } = useKnowledgeLocation(selected, folders)
  const models = ref<ChatModel[]>([])
  const documentSearch = ref(''),
    nextDocumentCursor = ref<string | null>(null),
    loadingMoreDocuments = ref(false)
  let documentSearchTimer: ReturnType<typeof setTimeout> | undefined
  const loading = ref(false),
    detailLoading = ref(false),
    listError = ref(''),
    detailError = ref(''),
    observationError = ref(''),
    actionError = ref('')
  const capabilities = ref<Awaited<ReturnType<typeof api.getCapabilities>> | null>(null)
  const graphEnabled = computed(
    () => capabilities.value?.graph_available === true && view.value === 'graph'
  )
  const graphWorkspace = useKnowledgeGraph(baseId, graphEnabled)
  const actionBusy = ref(false),
    uncertain = ref(false)
  const query = useKnowledgeQuery(baseId, selected)
  const {
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
  } = useKnowledgeUpload({
    baseId,
    selected,
    folderId,
    actionBusy,
    uncertain,
    onDenied: (error) => denyProtectedDetail(error),
    onAccepted: async (job, destinationFolder) => {
      if (job.base_id !== baseId.value) return
      await selectFolder(destinationFolder)
      await acceptJob(job)
    }
  })
  const scope = new RequestScope(),
    detailScope = new RequestScope(),
    writeScope = new RequestScope(),
    optionsScope = new RequestScope(),
    previewScope = new RequestScope(),
    referencesScope = new RequestScope(),
    accessScope = new RequestScope()
  const jobObservation = useKnowledgeJobObservation({
    baseId,
    selected,
    jobs,
    observationError,
    syncDetail: (observer) => loadDetail(true, observer),
    syncOverview: (observer) => loadList(true, observer),
    permissionDenied,
    onDenied: denyProtectedDetail,
    onCompleted: onJobCompleted
  })
  const busy = computed(
    () => actionBusy.value || uploading.value || !!selected.value?.active_job_id
  )
  const editor = reactive({
    open: false,
    kind: 'create' as EditorKind,
    base: null as KnowledgeBase | null,
    folder: null as KnowledgeFolder | null,
    doc: null as KnowledgeDocument | null,
    name: '',
    description: '',
    embedModel: '',
    size: 1000 as number | null,
    overlap: 200 as number | null,
    key: ''
  })
  const editorError = ref('')
  const preview = reactive({ open: false, name: '', loading: false, text: '', error: '' })
  const embeddingOptions = computed(() =>
    models.value
      .filter((model) => model.enabled && model.model_type === 'embedding')
      .map((model) => ({ value: model.id, label: model.name }))
  )
  async function loadList(silent = false, observer?: ContextTicket): Promise<boolean> {
    const ticket = scope.replace()
    const signal = observer ? AbortSignal.any([ticket.signal, observer.signal]) : ticket.signal
    const isCurrent = () => ticket.isCurrent() && (!observer || observer.isCurrent())
    if (!silent) loading.value = true
    listError.value = ''
    try {
      const rows = await api.listBases(signal)
      if (!isCurrent()) return false
      bases.value = rows
      if (
        selected.value &&
        rows.some((row) => row.id === selected.value?.id && row.availability !== 'available')
      ) {
        const row = rows.find((item) => item.id === selected.value?.id)!
        denyProtectedDetail(
          new ApiError(
            'knowledge_owner_unavailable',
            row.availability === 'owner_deleted' ? '创建者已删除' : '创建者已停用',
            404
          ),
          false
        )
        bases.value = rows
      }
      return true
    } catch (error) {
      if (isCurrent() && !isAborted(error)) {
        listError.value = errorMessage(error)
        if (permissionDenied(error)) {
          bases.value = []
          denyProtectedDetail(error)
        } else if (silent) observationError.value = errorMessage(error)
      }
      return false
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  }
  function denyProtectedDetail(error: unknown, refreshList = true): void {
    retainAndResetUpload()
    accessScope.replace()
    scope.replace()
    jobObservation.stop()
    detailScope.replace()
    previewScope.replace()
    writeScope.replace()
    selected.value = null
    documents.value = []
    nextDocumentCursor.value = null
    loadingMoreDocuments.value = false
    folders.value = []
    jobs.value = []
    const unavailable = error instanceof ApiError && error.code === 'knowledge_owner_unavailable'
    bases.value = bases.value.filter((base) => base.id !== baseId.value)
    if (unavailable && refreshList) void loadList(true)
    folderId.value = null
    Object.assign(preview, { open: false, loading: false, text: '', error: '' })
    Object.assign(editor, {
      open: false,
      base: null,
      folder: null,
      doc: null,
      name: '',
      description: ''
    })
    Object.assign(deletion, { open: false, item: null })
    actionBusy.value = false
    loading.value = false
    query.resetQuery()
    graphWorkspace.denyProtectedResults()
    detailLoading.value = false
    observationError.value = ''
    detailError.value = errorMessage(error)
  }
  function permissionDenied(error: unknown): boolean {
    return error instanceof ApiError && [401, 403, 404].includes(error.status)
  }
  async function loadDetail(
    silent = false,
    observer?: ContextTicket,
    checkedBase?: KnowledgeBase
  ): Promise<boolean> {
    const id = baseId.value
    if (!id) return false
    clearTimeout(documentSearchTimer)
    const ticket = detailScope.replace()
    loadingMoreDocuments.value = false
    nextDocumentCursor.value = null
    const signal = observer ? AbortSignal.any([ticket.signal, observer.signal]) : ticket.signal
    const isCurrent = () => ticket.isCurrent() && (!observer || observer.isCurrent())
    if (!silent) {
      detailLoading.value = true
      detailError.value = ''
    }
    try {
      const base = checkedBase?.id === id ? checkedBase : await api.getBase(id, signal)
      const [dirs, taskRows] = await Promise.all([
        api.listFolders(id, signal),
        base.can_manage ? api.listJobs(id, signal) : Promise.resolve([])
      ])
      if (!isCurrent() || id !== baseId.value) return false
      const directory = folderFromRoute(dirs)
      const docs = await api.listDocuments(
        id,
        signal,
        view.value === 'graph'
          ? undefined
          : {
              folderId: directory,
              search: directory === folderId.value ? documentSearch.value : ''
            }
      )
      if (!isCurrent() || id !== baseId.value) return false
      const previous = selected.value
      const vectorResult = query.queryResult.value
      const graphResult = query.graphQuery.result.value
      const onPage = new Set(
        docs.items.filter((doc) => doc.status !== 'deleting').map((doc) => doc.id)
      )
      const sources = new Set([
        ...(vectorResult?.items
          .filter((hit) => hit.knowledge_base_id === id)
          .map((hit) => hit.document_id) ?? []),
        ...(graphResult?.sources.map((source) => source.document_id) ?? [])
      ])
      const missing = [...sources].filter((documentId) => !onPage.has(documentId))
      const visible = missing.length ? await api.visibleDocumentIds(id, missing, signal) : []
      if (!isCurrent() || id !== baseId.value) return false
      const lostSource =
        visible.length < missing.length &&
        vectorResult === query.queryResult.value &&
        graphResult === query.graphQuery.result.value
      if (
        previous?.id === base.id &&
        (previous.acl_version !== base.acl_version ||
          previous.active_revision !== base.active_revision ||
          lostSource)
      )
        query.resetQuery()
      selected.value = base
      documents.value = docs.items
      nextDocumentCursor.value = docs.nextCursor
      folders.value = dirs
      jobs.value = taskRows
      syncFolderDocuments()
      detailError.value = ''
      return true
    } catch (error) {
      if (isCurrent() && !isAborted(error)) {
        if (permissionDenied(error)) denyProtectedDetail(error)
        else if (silent) observationError.value = errorMessage(error)
        else detailError.value = errorMessage(error)
      }
      return false
    } finally {
      if (ticket.isCurrent()) detailLoading.value = false
    }
  }
  function searchDocuments(value: string): void {
    const search = value.trim()
    if (search === documentSearch.value) return
    documentSearch.value = search
    if (view.value === 'graph') return
    detailScope.replace()
    loadingMoreDocuments.value = false
    nextDocumentCursor.value = null
    clearTimeout(documentSearchTimer)
    documentSearchTimer = setTimeout(() => {
      if (selected.value?.id === baseId.value && view.value !== 'graph') void loadDetail(true)
    }, 250)
  }
  async function loadMoreDocuments(): Promise<void> {
    const id = baseId.value
    const cursor = nextDocumentCursor.value
    if (!cursor || loadingMoreDocuments.value || selected.value?.id !== id) return
    const ticket = detailScope.current()
    loadingMoreDocuments.value = true
    try {
      const page = await api.listDocuments(id, ticket.signal, {
        folderId: folderId.value,
        search: documentSearch.value,
        cursor
      })
      if (!ticket.isCurrent() || id !== baseId.value) return
      const loaded = new Set(documents.value.map((doc) => doc.id))
      documents.value = [...documents.value, ...page.items.filter((doc) => !loaded.has(doc.id))]
      nextDocumentCursor.value = page.nextCursor
      detailError.value = ''
    } catch (error) {
      if (!ticket.isCurrent() || isAborted(error)) return
      if (permissionDenied(error)) denyProtectedDetail(error)
      else detailError.value = errorMessage(error)
    } finally {
      if (ticket.isCurrent()) loadingMoreDocuments.value = false
    }
  }
  async function refresh(): Promise<void> {
    accessScope.replace()
    const observer = jobObservation.stop()
    await Promise.all([loadList(false, observer), loadDetail(false, observer)])
    if (observer.isCurrent()) void jobObservation.observe()
  }
  async function acceptJob(job: KnowledgeJob): Promise<void> {
    if (job.base_id !== baseId.value || !selected.value) return
    jobObservation.notifyCompleted(job)
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)]
    selected.value = {
      ...selected.value,
      active_job_id: jobFinished(job) ? null : job.id
    }
    await refresh()
  }
  function syncFolderDocuments(): boolean {
    if (!synchronizeFolderLocation()) return false
    documentSearch.value = ''
    clearTimeout(documentSearchTimer)
    return true
  }
  watch(folderQuery, () => {
    if (syncFolderDocuments() && view.value !== 'graph') {
      documents.value = []
      nextDocumentCursor.value = null
      void loadDetail()
    }
  })
  function openEditor(
    kind: EditorKind,
    item?: KnowledgeBase | KnowledgeFolder | KnowledgeDocument
  ): void {
    if (
      !canUseCapability(
        'knowledge',
        kind === 'create' || kind === 'createFolder' ? 'create' : 'update'
      ) ||
      actionBusy.value ||
      uploading.value
    )
      return
    editorError.value = ''
    uncertain.value = false
    Object.assign(editor, {
      open: true,
      kind,
      base: null,
      folder: null,
      doc: null,
      name: '',
      description: '',
      embedModel: '',
      size: selected.value?.chunk_size ?? 1000,
      overlap: selected.value?.chunk_overlap ?? 200,
      key: createRequestId()
    })
    if (kind === 'base' && item && 'embed_model_id' in item) {
      editor.base = item
      editor.name = item.name
      editor.description = item.description
    }
    if (kind === 'folder' && item && 'parent_id' in item) {
      editor.folder = item
      editor.name = item.name
    }
    if (kind === 'rechunk' && item && 'file_id' in item) {
      editor.doc = item
      editor.size = item.chunk_size
      editor.overlap = item.chunk_overlap
    }
  }
  async function saveEditor(): Promise<void> {
    if (actionBusy.value || (uncertain.value && editor.kind !== 'rechunk')) return
    const chunked = editor.kind === 'rechunk'
    if (chunked && !validChunks(editor.size, editor.overlap)) {
      editorError.value = '请填写有效的切片大小与重叠大小'
      return
    }
    if (editor.kind !== 'rechunk' && !editor.name.trim()) {
      editorError.value = '请填写名称'
      return
    }
    if (
      editor.kind === 'create' &&
      (!capabilities.value?.default_embedding_configured ||
        !capabilities.value?.default_rerank_configured)
    ) {
      editorError.value = '请联系管理员配置有效的默认向量模型和重排模型'
      return
    }
    const ticket = writeScope.replace(),
      id = baseId.value
    actionBusy.value = true
    editorError.value = ''
    try {
      let accepted: KnowledgeJob | undefined
      if (editor.kind === 'create')
        await api.createBase(
          {
            name: editor.name.trim(),
            description: editor.description.trim()
          },
          ticket.signal
        )
      else if (editor.kind === 'base' && editor.base)
        await api.updateBase(
          editor.base,
          { name: editor.name.trim(), description: editor.description.trim() },
          ticket.signal
        )
      else if (editor.kind === 'createFolder')
        await api.createFolder(id, editor.name.trim(), folderId.value, ticket.signal)
      else if (editor.kind === 'folder' && editor.folder)
        await api.renameFolder(editor.folder, editor.name.trim(), ticket.signal)
      else if (editor.kind === 'rechunk' && editor.doc)
        accepted = await api.rechunkDocument(
          editor.doc,
          {
            chunk_size: editor.size!,
            chunk_overlap: editor.overlap!,
            client_request_id: editor.key
          },
          ticket.signal
        )
      if (!ticket.isCurrent()) return
      editor.open = false
      uncertain.value = false
      if (accepted) await acceptJob(accepted)
      else await refresh()
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        editorError.value = errorMessage(error)
        uncertain.value = uncertain.value || !isDefiniteRejection(error)
      }
    } finally {
      if (ticket.isCurrent()) actionBusy.value = false
    }
  }
  const deletion = reactive({
    open: false,
    kind: 'base' as 'base' | 'folder' | 'document' | 'rebuild',
    item: null as KnowledgeBase | KnowledgeFolder | KnowledgeDocument | null,
    key: '',
    loadingReferences: false,
    referencesError: false,
    confirmReferences: false,
    references: null as Awaited<ReturnType<typeof api.baseReferences>> | null
  })
  async function askDelete(
    kind: typeof deletion.kind,
    item: NonNullable<typeof deletion.item>
  ): Promise<void> {
    if (actionBusy.value || uploading.value) return
    actionError.value = ''
    uncertain.value = false
    const ticket = referencesScope.replace()
    Object.assign(deletion, {
      open: true,
      kind,
      item,
      key: createRequestId(),
      references: null,
      confirmReferences: false,
      loadingReferences: kind === 'base',
      referencesError: false
    })
    if (kind !== 'base') return
    try {
      const result = await api.baseReferences(item.id, ticket.signal)
      if (ticket.isCurrent() && deletion.open && deletion.item?.id === item.id)
        deletion.references = result
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        deletion.referencesError = true
        actionError.value = errorMessage(reason)
      }
    } finally {
      if (ticket.isCurrent()) deletion.loadingReferences = false
    }
  }
  async function confirmDelete(): Promise<void> {
    if (!deletion.item || actionBusy.value) return
    if (
      deletion.kind === 'base' &&
      (deletion.loadingReferences ||
        deletion.referencesError ||
        !deletion.references ||
        (deletion.references.total > 0 && !deletion.confirmReferences))
    )
      return
    const ticket = writeScope.replace(),
      item = deletion.item
    actionBusy.value = true
    actionError.value = ''
    try {
      let accepted: KnowledgeJob | undefined
      if (deletion.kind === 'base' && 'embed_model_id' in item)
        await api.deleteBase(item, deletion.key, ticket.signal, deletion.confirmReferences)
      else if (deletion.kind === 'rebuild' && 'embed_model_id' in item)
        accepted = await api.rebuildBase(item, deletion.key, ticket.signal)
      else if (deletion.kind === 'folder' && 'parent_id' in item)
        accepted = await api.deleteFolder(item, deletion.key, ticket.signal)
      else if ('file_id' in item)
        accepted = await api.deleteDocument(item, deletion.key, ticket.signal)
      if (!ticket.isCurrent()) return
      deletion.open = false
      uncertain.value = false
      if (deletion.kind === 'base') await back()
      if (accepted) await acceptJob(accepted)
      else await refresh()
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        actionError.value = errorMessage(error)
        uncertain.value = uncertain.value || !isDefiniteRejection(error)
      }
    } finally {
      if (ticket.isCurrent()) actionBusy.value = false
    }
  }
  const retryIntents = new Map<string, { document: KnowledgeDocument; requestId: string }>()
  async function retryDocument(doc: KnowledgeDocument): Promise<void> {
    if (busy.value || !selected.value?.can_manage) return
    const ticket = writeScope.replace()
    actionBusy.value = true
    actionError.value = ''
    const confirming = retryIntents.has(doc.id)
    const intent = retryIntents.get(doc.id) ?? {
      document: { ...doc },
      requestId: createRequestId()
    }
    retryIntents.set(doc.id, intent)
    try {
      const job = await api.rechunkDocument(
        intent.document,
        {
          chunk_size: intent.document.chunk_size,
          chunk_overlap: intent.document.chunk_overlap,
          client_request_id: intent.requestId
        },
        ticket.signal
      )
      if (ticket.isCurrent()) {
        retryIntents.delete(doc.id)
        await acceptJob(job)
      }
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        const unknown = confirming || !isDefiniteRejection(reason)
        if (!unknown) retryIntents.delete(doc.id)
        actionError.value = errorMessage(reason) + (unknown ? '；再次重试会确认原提交。' : '')
      }
    } finally {
      if (ticket.isCurrent()) actionBusy.value = false
    }
  }
  async function cancelJob(job: KnowledgeJob): Promise<void> {
    if (actionBusy.value || !job.cancel_allowed) return
    const ticket = writeScope.replace()
    actionBusy.value = true
    actionError.value = ''
    try {
      const cancelled = await api.cancelJob(job.id, ticket.signal)
      if (ticket.isCurrent()) await acceptJob(cancelled)
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) actionError.value = errorMessage(error)
    } finally {
      if (ticket.isCurrent()) actionBusy.value = false
    }
  }
  async function showPreview(doc: KnowledgeDocument | KnowledgeHit): Promise<void> {
    const ticket = previewScope.replace()
    Object.assign(preview, {
      open: true,
      name: 'file_name' in doc ? doc.file_name : doc.title,
      loading: true,
      text: '',
      error: ''
    })
    try {
      const result = await api.previewDocument(
        'base_id' in doc ? doc.base_id : doc.knowledge_base_id,
        'file_name' in doc ? doc.id : doc.document_id,
        ticket.signal
      )
      if (ticket.isCurrent() && preview.open) preview.text = result.text
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        if (error instanceof ApiError && error.code === 'knowledge_owner_unavailable') {
          denyProtectedDetail(error)
          return
        }
        preview.error = errorMessage(error)
        if (
          query.graphTarget.value &&
          error instanceof ApiError &&
          [401, 403, 404].includes(error.status)
        )
          query.graphQuery.denyProtectedResults(
            '来源权限已变化，图谱结果已清除，请恢复查看后再使用'
          )
      }
    } finally {
      if (ticket.isCurrent()) preview.loading = false
    }
  }
  watch(
    baseId,
    async (id) => {
      accessScope.replace()
      detailScope.replace()
      jobObservation.reset()
      query.resetQuery()
      previewScope.replace()
      writeScope.replace()
      actionBusy.value = false
      resetUpload()
      uncertain.value = false
      selected.value = null
      folders.value = []
      documents.value = []
      nextDocumentCursor.value = null
      documentSearch.value = ''
      clearTimeout(documentSearchTimer)
      jobs.value = []
      folderId.value = null
      detailError.value = ''
      observationError.value = ''
      actionError.value = ''
      editor.open = false
      deletion.open = false
      preview.open = false
      if (id) {
        await loadDetail()
        if (id !== baseId.value || !selected.value) return
        void jobObservation.observe()
        void query.restoreSelectedQuery(id)
      }
    },
    { immediate: true }
  )
  async function revalidateAccess(refreshDetails = false): Promise<void> {
    const id = baseId.value,
      ticket = accessScope.replace()
    if (!id || selected.value?.id !== id) return
    let failures = 0
    while (ticket.isCurrent() && id === baseId.value) {
      try {
        const fresh = await api.getBase(id, ticket.signal)
        const current = selected.value
        if (!ticket.isCurrent() || id !== baseId.value || current?.id !== id) return
        if (fresh.version < current.version || fresh.acl_version < current.acl_version) return
        const changed =
          fresh.version !== current.version ||
          fresh.acl_version !== current.acl_version ||
          fresh.active_revision !== current.active_revision ||
          fresh.active_job_id !== current.active_job_id ||
          fresh.can_manage !== current.can_manage
        if (
          fresh.acl_version !== current.acl_version ||
          fresh.active_revision !== current.active_revision
        )
          query.resetQuery()
        if (refreshDetails || changed || fresh.document_policy === 'legacy') {
          if (!(await loadDetail(true, ticket, fresh))) {
            if (!ticket.isCurrent()) return
            throw new Error('知识库状态暂未同步')
          }
          if (ticket.isCurrent()) void jobObservation.observe()
        } else selected.value = fresh
        if (ticket.isCurrent()) observationError.value = ''
        return
      } catch (error) {
        if (!ticket.isCurrent() || id !== baseId.value || isAborted(error)) return
        if (permissionDenied(error)) {
          denyProtectedDetail(error)
          return
        }
        observationError.value = errorMessage(error)
        try {
          await waitForBackgroundObservation(ticket.signal, Math.min(1500 * 2 ** failures, 30000))
        } catch {
          return
        }
        failures = Math.min(failures + 1, 5)
      }
    }
  }
  watch(view, (next, previous) => {
    void revalidateAccess(next === 'graph' || previous === 'graph')
  })
  const initial = optionsScope.current()
  void Promise.allSettled([
    api.getCapabilities(initial.signal).then((value) => {
      if (initial.isCurrent()) capabilities.value = value
    }),
    (canUseModule('knowledge') ? getModels(initial.signal) : Promise.resolve([])).then((value) => {
      if (initial.isCurrent()) models.value = value
    })
  ]).then((results) => {
    if (!initial.isCurrent()) return
    const failed = results.find((result) => result.status === 'rejected')
    if (failed?.status === 'rejected' && !isAborted(failed.reason))
      listError.value = errorMessage(failed.reason)
  })
  const resumeAccess = () => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
    void revalidateAccess()
    void loadList(true)
  }
  if (typeof window !== 'undefined') window.addEventListener('focus', resumeAccess)
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', resumeAccess)
  void loadList()
  onBeforeUnmount(() => {
    clearTimeout(documentSearchTimer)
    if (typeof window !== 'undefined') window.removeEventListener('focus', resumeAccess)
    if (typeof document !== 'undefined')
      document.removeEventListener('visibilitychange', resumeAccess)
    for (const owner of [
      scope,
      detailScope,
      writeScope,
      optionsScope,
      previewScope,
      referencesScope,
      accessScope
    ])
      owner.dispose()
  })
  return {
    ...query,
    graphWorkspace,
    baseId,
    view,
    bases,
    selected,
    folders,
    documents,
    nextDocumentCursor,
    loadingMoreDocuments,
    loadMoreDocuments,
    searchDocuments,
    jobs,
    folderId,
    selectFolder,
    models,
    embeddingOptions,
    capabilities,
    loading,
    detailLoading,
    listError,
    detailError,
    observationError,
    actionError,
    actionBusy,
    busy,
    uncertain,
    editor,
    editorError,
    deletion,
    preview,
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
    loadList,
    loadDetail,
    refresh,
    acceptJob,
    navigate,
    back,
    openEditor,
    saveEditor,
    askDelete,
    confirmDelete,
    cancelJob,
    retryDocument,
    showPreview,
    openUpload,
    selectUploadFolder,
    closeUpload,
    queueFile,
    submitUpload
  }
}
