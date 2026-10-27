import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import type { ManagedFile } from '../../files/public.ts'
import { getFile } from '../../files/public.ts'
import * as api from '../api/index.ts'
import type {
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeFolder,
  KnowledgeHit,
  KnowledgeJob
} from '../api/index.ts'
import { validChunks } from '../presentation.ts'
import { useKnowledgeQuery } from './useKnowledgeQuery.ts'
import { useKnowledgeUpload } from './useKnowledgeUpload.ts'

type EditorKind = 'create' | 'base' | 'createFolder' | 'folder' | 'rechunk'

export function useKnowledgeWorkspace() {
  const route = useRoute(),
    router = useRouter()
  const baseId = computed(() =>
    typeof route.params.baseId === 'string' ? route.params.baseId : ''
  )
  const view = computed(() => (route.params.view === 'query' ? 'query' : 'documents'))
  const bases = ref<KnowledgeBase[]>([]),
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
    actionError = ref('')
  const capabilities = ref<Awaited<ReturnType<typeof api.getCapabilities>> | null>(null)
  const actionBusy = ref(false),
    uncertain = ref(false)
  const query = useKnowledgeQuery(baseId, selected)
  const {
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
  } = useKnowledgeUpload({ baseId, selected, folderId, actionBusy, uncertain, onAccepted: refresh })
  const scope = new RequestScope(),
    detailScope = new RequestScope(),
    writeScope = new RequestScope(),
    pollScope = new RequestScope(),
    optionsScope = new RequestScope(),
    previewScope = new RequestScope(),
    sourceScope = new RequestScope()
  const busy = computed(
    () => actionBusy.value || uploading.value || !!selected.value?.active_job_id
  )
  const sourceAclFile = ref<ManagedFile | null>(null)
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
      .filter((model) => model.enabled && model.published && model.model_type === 'embedding')
      .map((model) => ({ value: model.id, label: model.name }))
  )
  async function loadList(): Promise<void> {
    const ticket = scope.replace()
    loading.value = true
    listError.value = ''
    try {
      const rows = await api.listBases(ticket.signal)
      if (ticket.isCurrent()) bases.value = rows
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) listError.value = errorMessage(error)
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  }
  async function loadDetail(): Promise<void> {
    const id = baseId.value
    if (!id) return
    const ticket = detailScope.replace()
    detailLoading.value = true
    detailError.value = ''
    try {
      const base = await api.getBase(id, ticket.signal)
      const [docs, dirs, taskRows] = await Promise.all([
        api.listDocuments(id, ticket.signal),
        api.listFolders(id, ticket.signal),
        base.can_manage ? api.listJobs(id, ticket.signal) : Promise.resolve([])
      ])
      if (!ticket.isCurrent() || id !== baseId.value) return
      selected.value = base
      documents.value = docs.items
      folders.value = dirs
      jobs.value = taskRows
      if (folderId.value && !dirs.some((dir) => dir.id === folderId.value)) folderId.value = null
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) detailError.value = errorMessage(error)
    } finally {
      if (ticket.isCurrent()) detailLoading.value = false
    }
  }
  async function pollJobs(): Promise<void> {
    const ticket = pollScope.replace(),
      id = baseId.value
    try {
      while (
        id &&
        id === baseId.value &&
        jobs.value.some(
          (job) =>
            !['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)
        )
      ) {
        await api.waitForObservation(ticket.signal, 1200)
        await loadDetail()
        if (!ticket.isCurrent()) return
      }
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) detailError.value = errorMessage(error)
    }
  }
  async function refresh(): Promise<void> {
    await Promise.all([loadList(), loadDetail()])
    void pollJobs()
  }
  async function navigate(base: KnowledgeBase, destination: string): Promise<void> {
    await router.push(
      `/knowledge/${encodeURIComponent(base.id)}/${destination === 'query' ? 'query' : 'documents'}`
    )
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
      key: crypto.randomUUID()
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
    const chunked = editor.kind === 'create' || editor.kind === 'rechunk'
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
      !embeddingOptions.value.some((option) => option.value === editor.embedModel)
    ) {
      editorError.value = '请选择可用的向量模型'
      return
    }
    const ticket = writeScope.replace(),
      id = baseId.value
    actionBusy.value = true
    editorError.value = ''
    try {
      if (editor.kind === 'create')
        await api.createBase(
          {
            name: editor.name.trim(),
            description: editor.description.trim(),
            embed_model_id: editor.embedModel,
            chunk_size: editor.size!,
            chunk_overlap: editor.overlap!,
            access: 'private',
            enable_knowledge_graph: false,
            llm_model_id: null
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
        await api.rechunkDocument(
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
      await refresh()
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
    key: ''
  })
  function askDelete(kind: typeof deletion.kind, item: NonNullable<typeof deletion.item>): void {
    if (actionBusy.value || uploading.value) return
    actionError.value = ''
    uncertain.value = false
    Object.assign(deletion, { open: true, kind, item, key: crypto.randomUUID() })
  }
  async function confirmDelete(): Promise<void> {
    if (!deletion.item || actionBusy.value) return
    const ticket = writeScope.replace(),
      item = deletion.item
    actionBusy.value = true
    actionError.value = ''
    try {
      if (deletion.kind === 'base' && 'embed_model_id' in item)
        await api.deleteBase(item, deletion.key, ticket.signal)
      else if (deletion.kind === 'rebuild' && 'embed_model_id' in item)
        await api.rebuildBase(item, deletion.key, ticket.signal)
      else if (deletion.kind === 'folder' && 'parent_id' in item)
        await api.deleteFolder(item, deletion.key, ticket.signal)
      else if ('file_id' in item) await api.deleteDocument(item, deletion.key, ticket.signal)
      if (!ticket.isCurrent()) return
      deletion.open = false
      uncertain.value = false
      if (deletion.kind === 'base') await back()
      await refresh()
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        actionError.value = errorMessage(error)
        uncertain.value = !(error instanceof ApiError) || error.status >= 500
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
      await api.cancelJob(job.id, ticket.signal)
      if (ticket.isCurrent()) await refresh()
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
      if (ticket.isCurrent() && !isAborted(error)) preview.error = errorMessage(error)
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
  const findUsers = (search: string, signal: AbortSignal) =>
    api.findKnowledgeUsers(baseId.value, search, signal)
  watch(
    baseId,
    async (id) => {
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
      folderId.value = null
      detailError.value = ''
      actionError.value = ''
      editor.open = false
      deletion.open = false
      preview.open = false
      if (id) {
        await loadDetail()
        if (id !== baseId.value || !selected.value) return
        void pollJobs()
        void query.restoreQuery(id)
      }
    },
    { immediate: true }
  )
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
  void loadList()
  onBeforeUnmount(() => {
    for (const owner of [
      scope,
      detailScope,
      writeScope,
      pollScope,
      optionsScope,
      previewScope,
      sourceScope
    ])
      owner.dispose()
  })
  return {
    ...query,
    baseId,
    view,
    bases,
    selected,
    folders,
    documents,
    jobs,
    folderId,
    models,
    embeddingOptions,
    capabilities,
    loading,
    detailLoading,
    listError,
    detailError,
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
    uploading,
    uploadDiscarding,
    uploadNotice,
    uploadError,
    uploadItems,
    uploadChunks,
    loadList,
    loadDetail,
    refresh,
    navigate,
    back,
    openEditor,
    saveEditor,
    askDelete,
    confirmDelete,
    cancelJob,
    showPreview,
    showSourceAcl,
    findUsers,
    openUpload,
    closeUpload,
    queueFile,
    submitUpload
  }
}
