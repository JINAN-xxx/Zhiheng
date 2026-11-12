import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message as notice } from 'ant-design-vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { waitForBackgroundObservation } from '../../../shared/backgroundObservation.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import type { ManagedFile } from '../../files/public.ts'
import { getFile } from '../../files/public.ts'
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

type EditorKind = 'create' | 'base' | 'createFolder' | 'folder' | 'rechunk'
const jobFinished = (job: KnowledgeJob) =>
  ['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)

export function useKnowledgeWorkspace(onJobCompleted?: (job: KnowledgeJob) => void) {
  const route = useRoute(),
    router = useRouter()
  const baseId = computed(() =>
    typeof route.params.baseId === 'string' ? route.params.baseId : ''
  )
  const view = computed(() =>
    route.params.view === 'graph' ? 'graph' : route.params.view === 'query' ? 'query' : 'documents'
  )
  const bases = ref<KnowledgeBaseListItem[]>([]),
    selected = ref<KnowledgeBase | null>(null)
  const folders = ref<KnowledgeFolder[]>([]),
    documents = ref<KnowledgeDocument[]>([]),
    jobs = ref<KnowledgeJob[]>([])
  const folderId = ref<string | null>(null),
    models = ref<ChatModel[]>([])
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
    pollScope = new RequestScope(),
    optionsScope = new RequestScope(),
    previewScope = new RequestScope(),
    sourceScope = new RequestScope(),
    referencesScope = new RequestScope(),
    accessScope = new RequestScope()
  const notifiedJobs = new Set<string>()
  function notifyCompleted(job: KnowledgeJob): void {
    if (job.base_id !== baseId.value || job.status !== 'completed' || notifiedJobs.has(job.id))
      return
    notifiedJobs.add(job.id)
    onJobCompleted?.(job)
  }
  watch(
    jobs,
    (next, previous) => {
      for (const job of next) {
        if (previous.some((item) => item.id === job.id && !jobFinished(item))) notifyCompleted(job)
      }
    },
    { flush: 'sync' }
  )
  const busy = computed(
    () => actionBusy.value || uploading.value || !!selected.value?.active_job_id
  )
  const sourceAclFile = ref<ManagedFile | null>(null)
  let disposed = false
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
    pollScope.replace()
    detailScope.replace()
    previewScope.replace()
    sourceScope.replace()
    writeScope.replace()
    selected.value = null
    documents.value = []
    folders.value = []
    jobs.value = []
    const unavailable = error instanceof ApiError && error.code === 'knowledge_owner_unavailable'
    bases.value = bases.value.filter((base) => base.id !== baseId.value)
    if (unavailable && refreshList) void loadList(true)
    sourceAclFile.value = null
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
  const permissionDenied = (error: unknown) =>
    error instanceof ApiError && [401, 403, 404].includes(error.status)
  async function loadDetail(
    silent = false,
    observer?: ContextTicket,
    checkedBase?: KnowledgeBase
  ): Promise<boolean> {
    const id = baseId.value
    if (!id) return false
    const ticket = detailScope.replace()
    const signal = observer ? AbortSignal.any([ticket.signal, observer.signal]) : ticket.signal
    const isCurrent = () => ticket.isCurrent() && (!observer || observer.isCurrent())
    if (!silent) {
      detailLoading.value = true
      detailError.value = ''
    }
    try {
      const base = checkedBase?.id === id ? checkedBase : await api.getBase(id, signal)
      const [docs, dirs, taskRows] = await Promise.all([
        api.listDocuments(id, signal),
        api.listFolders(id, signal),
        base.can_manage ? api.listJobs(id, signal) : Promise.resolve([])
      ])
      if (!isCurrent() || id !== baseId.value) return false
      const previous = selected.value
      const readableDocuments = new Set(
        docs.items.filter((doc) => doc.status !== 'deleting').map((doc) => doc.id)
      )
      const lostSource =
        query.queryResult.value?.items.some(
          (hit) => hit.knowledge_base_id === id && !readableDocuments.has(hit.document_id)
        ) ||
        query.graphQuery.result.value?.sources.some(
          (source) => !readableDocuments.has(source.document_id)
        )
      if (
        previous?.id === base.id &&
        (previous.acl_version !== base.acl_version ||
          previous.active_revision !== base.active_revision ||
          lostSource)
      )
        query.resetQuery()
      selected.value = base
      documents.value = docs.items
      folders.value = dirs
      jobs.value = taskRows
      synchronizeFolderLocation()
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
  async function pollJobs(): Promise<void> {
    if (disposed) return
    const ticket = pollScope.replace(),
      id = baseId.value
    if (!id || !selected.value?.can_manage) return
    const pendingIds = () => {
      const ids = jobs.value.filter((job) => !jobFinished(job)).map((job) => job.id)
      const activeId = selected.value?.active_job_id
      if (activeId && !jobs.value.some((job) => job.id === activeId)) ids.push(activeId)
      return [...new Set(ids)]
    }
    let failures = 0,
      needsDetailSync = false,
      needsOverviewSync = false
    try {
      while (
        ticket.isCurrent() &&
        id === baseId.value &&
        (needsDetailSync || needsOverviewSync || pendingIds().length)
      ) {
        await waitForBackgroundObservation(ticket.signal, Math.min(1500 * 2 ** failures, 30_000))
        try {
          if (!needsDetailSync && !needsOverviewSync) {
            const updates = await Promise.all(
              pendingIds().map((jobId) => api.getJob(jobId, ticket.signal))
            )
            if (!ticket.isCurrent() || id !== baseId.value) return
            for (const job of updates) {
              const current = jobs.value.find((item) => item.id === job.id)
              if (
                current &&
                (current.version > job.version || (jobFinished(current) && !jobFinished(job)))
              )
                continue
              if (!current) jobs.value = [job, ...jobs.value]
              else if (JSON.stringify(current) !== JSON.stringify(job))
                jobs.value = jobs.value.map((item) => (item.id === job.id ? job : item))
              if (jobFinished(job)) needsDetailSync = needsOverviewSync = true
            }
          }
          if (needsDetailSync) {
            const synced = await loadDetail(true, ticket)
            if (!ticket.isCurrent() || id !== baseId.value) return
            if (!synced) {
              failures = Math.min(failures + 1, 5)
              continue
            }
            needsDetailSync = false
          }
          if (needsOverviewSync) {
            const synced = await loadList(true, ticket)
            if (!ticket.isCurrent() || id !== baseId.value) return
            if (!synced) {
              failures = Math.min(failures + 1, 5)
              continue
            }
            needsOverviewSync = false
          }
          failures = 0
          observationError.value = ''
        } catch (error) {
          if (!ticket.isCurrent() || isAborted(error)) return
          if (permissionDenied(error)) {
            denyProtectedDetail(error)
            return
          }
          failures = Math.min(failures + 1, 5)
          observationError.value = errorMessage(error)
        }
      }
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) observationError.value = errorMessage(error)
    }
  }
  async function refresh(): Promise<void> {
    accessScope.replace()
    const observer = pollScope.replace()
    await Promise.all([loadList(false, observer), loadDetail(false, observer)])
    if (observer.isCurrent()) void pollJobs()
  }
  async function acceptJob(job: KnowledgeJob): Promise<void> {
    if (job.base_id !== baseId.value || !selected.value) return
    notifyCompleted(job)
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)]
    selected.value = {
      ...selected.value,
      active_job_id: jobFinished(job) ? null : job.id
    }
    await refresh()
  }
  const invalidFolderLocations = new Set<string>()
  function synchronizeFolderLocation(): void {
    if (selected.value?.id !== baseId.value) return
    const value = route.query.folder
    const valid =
      typeof value === 'string' &&
      value.length > 0 &&
      value.length <= 64 &&
      folders.value.some((folder) => folder.id === value && folder.base_id === baseId.value)
    folderId.value = valid ? value : null
    if (value === undefined) return
    if (!valid) {
      const key = `${baseId.value}:${JSON.stringify(value)}`
      if (!invalidFolderLocations.has(key)) {
        invalidFolderLocations.add(key)
        void notice.warning({
          key: 'knowledge-folder-location',
          content: '目录已失效或不属于当前知识库，已返回根目录',
          duration: 3
        })
      }
      const query = { ...route.query }
      delete query.folder
      void router.replace({ path: route.path, query, hash: route.hash })
    }
  }
  async function selectFolder(id: string | null): Promise<void> {
    if (selected.value?.id !== baseId.value) return
    if (
      id !== null &&
      !folders.value.some((folder) => folder.id === id && folder.base_id === baseId.value)
    )
      return
    const query = { ...route.query }
    if (id) query.folder = id
    else delete query.folder
    folderId.value = id
    await router.push({ path: route.path, query, hash: route.hash })
  }
  watch(() => route.query.folder, synchronizeFolderLocation)
  async function navigate(base: KnowledgeBase, destination: string): Promise<void> {
    const query: Record<string, string> = {}
    if (base.id === baseId.value && folderId.value) query.folder = folderId.value
    await router.push({
      path: `/knowledge/${encodeURIComponent(base.id)}/${destination === 'graph' ? 'graph' : destination === 'query' ? 'query' : 'documents'}`,
      query
    })
  }
  async function back(): Promise<void> {
    await router.push('/knowledge')
  }
  function openEditor(
    kind: EditorKind,
    item?: KnowledgeBase | KnowledgeFolder | KnowledgeDocument
  ): void {
    if (actionBusy.value || uploading.value) return
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
        uncertain.value = !(error instanceof ApiError) || error.status >= 500
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
        uncertain.value = !(error instanceof ApiError) || error.status >= 500
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
        const unknown = !(reason instanceof ApiError) || reason.status >= 500
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
  async function showSourceAcl(doc: KnowledgeDocument): Promise<void> {
    const ticket = sourceScope.replace(),
      id = baseId.value
    actionError.value = ''
    try {
      const file = await getFile(doc.file_id, ticket.signal)
      if (ticket.isCurrent() && id === baseId.value) sourceAclFile.value = file
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) actionError.value = errorMessage(error)
    }
  }
  watch(
    baseId,
    async (id) => {
      accessScope.replace()
      detailScope.replace()
      pollScope.replace()
      query.resetQuery()
      previewScope.replace()
      sourceScope.replace()
      writeScope.replace()
      sourceAclFile.value = null
      actionBusy.value = false
      resetUpload()
      uncertain.value = false
      selected.value = null
      folders.value = []
      documents.value = []
      jobs.value = []
      notifiedJobs.clear()
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
        void pollJobs()
        void query.restoreSelectedQuery(id)
      }
    },
    { immediate: true }
  )
  async function revalidateAccess(): Promise<void> {
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
        if (changed || fresh.document_policy === 'legacy') {
          if (!(await loadDetail(true, ticket, fresh))) {
            if (!ticket.isCurrent()) return
            throw new Error('知识库状态暂未同步')
          }
          if (ticket.isCurrent()) void pollJobs()
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
  watch(view, () => {
    void revalidateAccess()
  })
  const initial = optionsScope.current()
  void Promise.allSettled([
    api.getCapabilities(initial.signal).then((value) => {
      if (initial.isCurrent()) capabilities.value = value
    }),
    getModels(initial.signal).then((value) => {
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
    disposed = true
    if (typeof window !== 'undefined') window.removeEventListener('focus', resumeAccess)
    if (typeof document !== 'undefined')
      document.removeEventListener('visibilitychange', resumeAccess)
    for (const owner of [
      scope,
      detailScope,
      writeScope,
      pollScope,
      optionsScope,
      previewScope,
      sourceScope,
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
    sourceAclFile,
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
    showSourceAcl,
    openUpload,
    selectUploadFolder,
    closeUpload,
    queueFile,
    submitUpload
  }
}
