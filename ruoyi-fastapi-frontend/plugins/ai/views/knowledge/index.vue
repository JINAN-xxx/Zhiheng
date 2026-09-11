<template>
  <main class="app-container ai-knowledge-center">
    <KnowledgeOverview
      v-if="!requestedDbId"
      :databases="databases"
      :loading="listLoading"
      :error="listError"
      :permissions="permissions"
      @refresh="loadDatabases"
      @create="openCreate"
      @edit="openEdit('renameDb', $event)"
      @remove="askDelete('database', $event)"
      @open="openKnowledgeView"
    />

    <section v-else-if="!can('query')" class="knowledge-route-state">
      <WorkspaceToolbar title="知识库">
        <template #actions>
          <a-button @click="backToOverview">
            <template #icon><ArrowLeftOutlined /></template>
            返回知识库
          </a-button>
        </template>
      </WorkspaceToolbar>
      <WorkspaceState kind="forbidden" title="无权访问知识库工作页面" />
    </section>

    <section v-else-if="!selectedDb" class="knowledge-route-state">
      <WorkspaceToolbar title="知识库">
        <template #actions>
          <a-button @click="backToOverview">
            <template #icon><ArrowLeftOutlined /></template>
            返回知识库
          </a-button>
        </template>
      </WorkspaceToolbar>
      <WorkspaceState
        :kind="listLoading ? 'loading' : 'error'"
        :title="listLoading ? '正在加载知识库' : '知识库不存在或已被删除'"
        :message="listError"
        @retry="loadDatabases"
      />
    </section>

    <KnowledgeDocuments
      v-else-if="activeView === 'documents'"
      :database="selectedDb"
      :folders="folders"
      :documents="documents"
      :folder-id="selection.folderId"
      :loading="detailLoading"
      :error="detailError"
      :busy="busy"
      :permissions="permissions"
      @back="backToOverview"
      @refresh="loadDocuments"
      @navigate="navigateView"
      @select-folder="selection.folderId = $event"
      @create-folder="openEdit('createFolder')"
      @upload="openUpload"
      @preview="openPreview"
      @edit="openDocumentEdit"
      @remove="openDocumentRemoval"
    />

    <KnowledgeQuery
      v-else-if="activeView === 'query'"
      :database="selectedDb"
      :result="selection.queryResult"
      :loading="queryLoading"
      :error="queryError"
      :elapsed="queryElapsed"
      @back="backToOverview"
      @refresh="clearQuery"
      @navigate="navigateView"
      @query="runQuery"
    />

    <KnowledgeGraphPage
      v-else
      :database="selectedDb"
      :labels="graphLabels"
      :graph="graphData"
      :loading="graphLoading"
      :error="graphError"
      @back="backToOverview"
      @navigate="navigateView"
      @load="loadGraph"
    />

    <a-modal
      v-model:open="editor.open"
      :title="editorTitle"
      centered
      width="min(580px, calc(100vw - 32px))"
      :confirm-loading="actionBusy"
      :mask-closable="false"
      :closable="!actionBusy"
      :keyboard="!actionBusy"
      :ok-button-props="{ disabled: editorLoading }"
      @ok="saveEditor"
    >
      <a-alert v-if="editorError" class="section-alert" type="error" :message="editorError" show-icon />
      <a-spin :spinning="editorLoading">
        <a-form layout="vertical" :disabled="actionBusy || editorLoading">
          <template v-if="editor.kind === 'createDb'">
            <a-form-item label="知识库名称" required>
              <a-input v-model:value="databaseForm.database_name" aria-label="知识库名称" :maxlength="100" />
            </a-form-item>
            <a-form-item label="描述">
              <a-textarea v-model:value="databaseForm.description" aria-label="知识库描述" :rows="2" :maxlength="500" />
            </a-form-item>
            <a-form-item label="存储类型" required>
              <a-select v-model:value="databaseForm.kb_type" aria-label="存储类型" :options="storageTypes" @change="onStorageChange" />
            </a-form-item>
            <a-alert
              v-if="!storageTypes.some(item => item.available) && !editorLoading"
              class="section-alert"
              type="warning"
              message="暂无可用存储类型，请联系管理员完成知识库配置。"
              show-icon
            />
            <a-form-item label="Embedding 模型" required>
              <a-select
                v-model:value="databaseForm.embed_model_id"
                aria-label="Embedding 模型"
                :options="embeddingOptions"
                show-search
                option-filter-prop="label"
                placeholder="选择已发布的 Embedding 模型"
              />
            </a-form-item>
            <a-form-item>
              <a-checkbox
                v-model:checked="databaseForm.enable_knowledge_graph"
                :disabled="!graphAvailable || databaseForm.kb_type === 'lightrag'"
              >
                同时构建知识图谱
              </a-checkbox>
              <p v-if="!graphAvailable" class="form-hint">知识图谱服务尚未配置。</p>
            </a-form-item>
            <a-form-item v-if="databaseForm.enable_knowledge_graph" label="图谱对话模型" required>
              <a-select
                v-model:value="databaseForm.llm_model_id"
                aria-label="图谱对话模型"
                :options="chatOptions"
                show-search
                option-filter-prop="label"
                placeholder="选择已发布的对话模型"
              />
            </a-form-item>
          </template>
          <template v-else-if="editor.kind === 'rechunk'">
            <p>为「{{ editor.item?.file_name }}」重新生成切片。</p>
            <ChunkFields v-model:size="chunkForm.chunk_size" v-model:overlap="chunkForm.chunk_overlap" />
          </template>
          <template v-else>
            <a-form-item label="名称" required>
              <a-input v-model:value="nameForm.name" aria-label="名称" :maxlength="100" />
            </a-form-item>
            <a-form-item v-if="editor.kind === 'renameDb'" label="描述">
              <a-textarea v-model:value="nameForm.description" aria-label="知识库描述" :rows="2" :maxlength="500" />
            </a-form-item>
          </template>
        </a-form>
      </a-spin>
    </a-modal>

    <a-modal
      v-model:open="deletion.open"
      title="确认删除"
      centered
      :confirm-loading="actionBusy"
      :mask-closable="false"
      :closable="!actionBusy"
      :keyboard="!actionBusy"
      ok-text="删除"
      :ok-button-props="{ danger: true }"
      @ok="confirmDelete"
    >
      <a-alert v-if="deleteError" class="section-alert" type="error" :message="deleteError" show-icon />
      <p>确定删除「{{ deletion.item?.name || deletion.item?.file_name }}」？</p>
      <p class="form-hint">{{ deletionMessage }}</p>
    </a-modal>

    <a-modal
      v-model:open="uploadOpen"
      title="上传文档"
      centered
      width="min(640px, calc(100vw - 32px))"
      :footer="null"
      :mask-closable="!uploading"
      :closable="!uploading"
      :keyboard="!uploading"
    >
      <p class="form-hint">{{ selectedDb?.name }} / {{ uploadFolderName }} · 支持 PDF、Word、TXT、Markdown 等文档</p>
      <a-alert v-if="uploadError" class="section-alert" type="error" :message="uploadError" show-icon />
      <a-form layout="vertical" :disabled="uploading">
        <ChunkFields v-model:size="uploadParams.chunk_size" v-model:overlap="uploadParams.chunk_overlap" />
      </a-form>
      <a-upload-dragger
        multiple
        :before-upload="queueFile"
        :show-upload-list="false"
        :disabled="uploading"
        accept=".pdf,.doc,.docx,.txt,.md,.markdown,.csv,.xlsx,.xls,.ppt,.pptx,.html"
      >
        <p class="ant-upload-drag-icon"><InboxOutlined /></p>
        <p>点击选择或拖拽文档到此处</p>
      </a-upload-dragger>
      <div class="upload-results" aria-live="polite">
        <div v-for="item in uploadItems" :key="item.uid" class="upload-result">
          <div>
            <span class="upload-name">{{ item.file.name }}</span>
            <a-tag :color="item.status === 'failed' ? 'error' : item.status === 'done' ? 'success' : 'processing'">
              {{ uploadStatus(item.status) }}
            </a-tag>
          </div>
          <a-progress
            :percent="item.progress"
            :status="item.status === 'failed' ? 'exception' : item.status === 'done' ? 'success' : 'active'"
            size="small"
          />
          <p v-if="item.error" class="inline-error">{{ item.error }}</p>
          <a-button v-if="item.status === 'failed' && !uploading" type="link" @click="retryUpload(item)">重试</a-button>
        </div>
      </div>
    </a-modal>

    <a-modal
      v-model:open="preview.open"
      :title="preview.name"
      centered
      width="min(900px, calc(100vw - 32px))"
      :footer="null"
    >
      <a-spin :spinning="preview.loading">
        <a-alert v-if="preview.error" type="error" :message="preview.error" show-icon />
        <pre v-else class="document-preview">{{ preview.content || (preview.loading ? '正在读取文档…' : '文档暂无可预览内容') }}</pre>
      </a-spin>
    </a-modal>
  </main>
</template>

<script setup name="AiKnowledge">
import { ArrowLeftOutlined, InboxOutlined } from '@ant-design/icons-vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import feedback from '@/plugins/antdFeedback'
import useUserStore from '@/store/modules/user'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import * as api from '../../api/knowledge.js'
import {
  buildKnowledgeDatabasePayload,
  buildKnowledgeLocation,
  knowledgeFolderTrail,
  normalizeKnowledgeDatabases,
  normalizeKnowledgeGraph,
  normalizeKnowledgeQuery,
  normalizeKnowledgeTypes,
  normalizeKnowledgeView,
  resetKnowledgeSelection,
  unwrapKnowledgeResponse,
  validateChunkParams,
} from '../../utils/knowledge.js'
import ChunkFields from './components/ChunkFields.vue'
import KnowledgeDocuments from './components/KnowledgeDocuments.vue'
import KnowledgeGraphPage from './components/KnowledgeGraphPage.vue'
import KnowledgeOverview from './components/KnowledgeOverview.vue'
import KnowledgeQuery from './components/KnowledgeQuery.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const permissions = computed(() => userStore.permissions || [])
const can = action => permissions.value.includes('*:*:*') || permissions.value.includes('ai:knowledge:' + action)
const message = error => normalizeErrorMessage(error, '操作失败，请重试')
const queryValue = value => Array.isArray(value) ? value[0] : value

const databases = ref([])
const listLoading = ref(false)
const listError = ref('')
const databasesReady = ref(false)
const selection = reactive(resetKnowledgeSelection())
const requestedDbId = computed(() => String(queryValue(route.query.dbId) || ''))
const selectedDb = computed(() => databases.value.find(database => database.db_id === requestedDbId.value))
const activeView = computed(() => selection.tab)
const folders = ref([])
const documents = ref([])
const detailLoading = ref(false)
const detailError = ref('')
const actionBusy = ref(false)
const uploading = ref(false)
const busy = computed(() => actionBusy.value || uploading.value)
let generation = 0
let listGeneration = 0
let previewGeneration = 0
let disposed = false

async function loadDatabases() {
  if (!can('list')) return
  const requestId = ++listGeneration
  listLoading.value = true
  listError.value = ''
  try {
    const rows = normalizeKnowledgeDatabases(await api.listKnowledgeBases())
    if (requestId === listGeneration && !disposed) databases.value = rows
  } catch (error) {
    if (requestId === listGeneration && !disposed) listError.value = message(error)
  } finally {
    if (requestId === listGeneration && !disposed) {
      listLoading.value = false
      databasesReady.value = true
    }
  }
}

function resetKnowledgeContext(dbId = null) {
  generation += 1
  queryController?.abort()
  if (selection.tab === 'documents') uploadController?.abort()
  Object.assign(selection, resetKnowledgeSelection(dbId))
  folders.value = []
  documents.value = []
  detailError.value = ''
  clearQuery()
  graphLabels.value = []
  graphData.value = normalizeKnowledgeGraph({ nodes: [], edges: [] })
  graphError.value = ''
}

async function syncRoute() {
  if (!databasesReady.value || disposed) return
  const dbId = requestedDbId.value
  if (!dbId) {
    if (selection.dbId) resetKnowledgeContext()
    return
  }

  const database = databases.value.find(item => item.db_id === dbId)
  if (!database) {
    if (selection.dbId !== dbId) resetKnowledgeContext(dbId)
    return
  }

  const rawView = String(queryValue(route.query.view) || '')
  const view = normalizeKnowledgeView(rawView, database.enable_knowledge_graph)
  if (rawView !== view) {
    await router.replace(buildKnowledgeLocation(dbId, view))
    return
  }

  const shouldLoad = selection.dbId !== dbId || selection.tab !== view
  if (!shouldLoad) return
  if (selection.dbId !== dbId) resetKnowledgeContext(dbId)
  else {
    generation += 1
    queryController?.abort()
    if (selection.tab === 'documents' && view !== 'documents') uploadController?.abort()
  }
  selection.tab = view
  if (view === 'documents') await loadDocuments()
  else if (view === 'graph') await loadGraphWorkspace()
}

function openKnowledgeView({ database, view }) {
  router.push(buildKnowledgeLocation(database.db_id, view))
}

function navigateView(view) {
  if (!selection.dbId) return
  router.push(buildKnowledgeLocation(selection.dbId, view))
}

function backToOverview() {
  router.push('/ai/knowledge')
}

async function loadDocuments() {
  const dbId = selection.dbId
  if (!dbId) return
  const current = generation
  detailLoading.value = true
  detailError.value = ''
  try {
    const [folderResponse, documentResponse] = await Promise.all([
      api.listKnowledgeFolders(dbId),
      api.listKnowledgeDocuments(dbId),
    ])
    if (current !== generation || disposed) return
    folders.value = unwrapKnowledgeResponse(folderResponse).folders || []
    documents.value = unwrapKnowledgeResponse(documentResponse).documents || []
    if (selection.folderId && !folders.value.some(folder => folder.id === selection.folderId)) {
      selection.folderId = null
    }
  } catch (error) {
    if (current === generation && !disposed) detailError.value = message(error)
  } finally {
    if (current === generation && !disposed) detailLoading.value = false
  }
}

const editor = reactive({ open: false, kind: '', item: null })
const editorError = ref('')
const editorLoading = ref(false)
const storageTypes = ref([])
const embeddingModels = ref([])
const chatModels = ref([])
const databaseForm = reactive({
  database_name: '',
  description: '',
  kb_type: '',
  embed_model_id: undefined,
  llm_model_id: undefined,
  enable_knowledge_graph: false,
})
const nameForm = reactive({ name: '', description: '' })
const chunkForm = reactive({ chunk_size: 1000, chunk_overlap: 200 })
const modelOptions = models => models.map(model => ({
  value: model.model_id,
  label: model.display_name || model.model_code || model.model_id,
}))
const embeddingOptions = computed(() => modelOptions(embeddingModels.value))
const chatOptions = computed(() => modelOptions(chatModels.value))
const graphAvailable = computed(() => storageTypes.value.some(type => type.value === 'lightrag' && type.available))
const editorTitle = computed(() => ({
  createDb: '创建知识库',
  renameDb: '编辑知识库',
  createFolder: '新建文件夹',
  renameFolder: '重命名文件夹',
  rechunk: '重新切片',
})[editor.kind])

async function openCreate() {
  editor.kind = 'createDb'
  editor.item = null
  editor.open = true
  editorError.value = ''
  editorLoading.value = true
  storageTypes.value = []
  embeddingModels.value = []
  chatModels.value = []
  Object.assign(databaseForm, {
    database_name: '',
    description: '',
    kb_type: '',
    embed_model_id: undefined,
    llm_model_id: undefined,
    enable_knowledge_graph: false,
  })
  try {
    const [types, embeddings, chats] = await Promise.all([
      api.listKnowledgeTypes(),
      api.listKnowledgeModels('embedding'),
      api.listKnowledgeModels('chat'),
    ])
    if (disposed) return
    storageTypes.value = normalizeKnowledgeTypes(types)
    embeddingModels.value = unwrapKnowledgeResponse(embeddings).models || []
    chatModels.value = unwrapKnowledgeResponse(chats).models || []
    databaseForm.kb_type = storageTypes.value.find(type => type.available)?.value || ''
    onStorageChange()
  } catch (error) {
    if (!disposed) editorError.value = message(error)
  } finally {
    if (!disposed) editorLoading.value = false
  }
}

function onStorageChange() {
  if (databaseForm.kb_type === 'lightrag') databaseForm.enable_knowledge_graph = true
  else if (!graphAvailable.value) databaseForm.enable_knowledge_graph = false
}

function openEdit(kind, item = null) {
  editor.kind = kind
  editor.item = item
  editor.open = true
  editorError.value = ''
  nameForm.name = item?.name || ''
  nameForm.description = item?.description || ''
  chunkForm.chunk_size = item?.chunk_size || 1000
  chunkForm.chunk_overlap = item?.chunk_overlap ?? 200
}

function openDocumentEdit({ kind, item }) {
  openEdit(kind, item)
}

async function saveEditor() {
  if (actionBusy.value || editorLoading.value) return
  editorError.value = ''
  actionBusy.value = true
  try {
    let result
    if (editor.kind === 'createDb') {
      if (!storageTypes.value.some(type => type.value === databaseForm.kb_type && type.available)) {
        throw new Error('请选择可用的存储类型')
      }
      result = await api.createKnowledgeBase(buildKnowledgeDatabasePayload(databaseForm))
    } else if (editor.kind === 'rechunk') {
      result = await api.rechunkKnowledgeDocument(selection.dbId, editor.item.id, validateChunkParams(chunkForm))
    } else {
      const name = nameForm.name.trim()
      if (!name) throw new Error('请输入名称')
      if (editor.kind === 'renameDb') {
        result = await api.updateKnowledgeBase(editor.item.db_id, { name, description: nameForm.description.trim() })
      } else if (editor.kind === 'createFolder') {
        result = await api.createKnowledgeFolder({
          name,
          db_id: selection.dbId,
          ...(selection.folderId ? { parent_id: selection.folderId } : {}),
        })
      } else {
        result = await api.renameKnowledgeFolder(selection.dbId, editor.item.id, name)
      }
    }

    const data = unwrapKnowledgeResponse(result)
    editor.open = false
    feedback.msgSuccess('已保存')
    if (editor.kind === 'createDb') {
      await loadDatabases()
      const dbId = data.db_id || data.database?.db_id
      if (!dbId) {
        feedback.msgWarning('知识库已创建，请从列表进入文档管理')
        return
      }
      await router.push(buildKnowledgeLocation(dbId, 'documents'))
    } else if (editor.kind === 'renameDb') {
      await loadDatabases()
    } else {
      await loadDocuments()
    }
  } catch (error) {
    editorError.value = message(error)
  } finally {
    actionBusy.value = false
  }
}

const deletion = reactive({ open: false, kind: '', item: null })
const deleteError = ref('')
const deletionMessage = computed(() => (
  deletion.kind === 'folder'
    ? '文件夹及其中的子目录和文档将一并删除。此操作无法撤销。'
    : deletion.kind === 'database'
      ? '请先清空库内文档。删除后无法恢复知识库。'
      : '文档及其索引将被删除，此操作无法撤销。'
))

function askDelete(kind, item) {
  deletion.open = true
  deletion.kind = kind
  deletion.item = item
  deleteError.value = ''
}

function openDocumentRemoval({ kind, item }) {
  askDelete(kind, item)
}

async function confirmDelete() {
  if (actionBusy.value) return
  actionBusy.value = true
  deleteError.value = ''
  try {
    const item = deletion.item
    let response
    if (deletion.kind === 'database') response = await api.deleteKnowledgeBase(item.db_id)
    else if (deletion.kind === 'folder') response = await api.deleteKnowledgeFolder(selection.dbId, item.id)
    else response = await api.deleteKnowledgeDocument(selection.dbId, item.id)
    unwrapKnowledgeResponse(response)
    deletion.open = false
    feedback.msgSuccess('已删除')
    if (deletion.kind === 'database') await loadDatabases()
    else await loadDocuments()
  } catch (error) {
    deleteError.value = message(error)
  } finally {
    actionBusy.value = false
  }
}

const preview = reactive({ open: false, name: '', loading: false, error: '', content: '' })

async function openPreview(document) {
  const requestId = ++previewGeneration
  Object.assign(preview, { open: true, name: document.file_name, loading: true, error: '', content: '' })
  try {
    const data = unwrapKnowledgeResponse(await api.previewKnowledgeDocument(selection.dbId, document.id))
    if (requestId === previewGeneration && !disposed) preview.content = String(data.content || '')
  } catch (error) {
    if (requestId === previewGeneration && !disposed) preview.error = message(error)
  } finally {
    if (requestId === previewGeneration && !disposed) preview.loading = false
  }
}

const uploadOpen = ref(false)
const uploadItems = ref([])
const uploadError = ref('')
const uploadParams = reactive({ chunk_size: 1000, chunk_overlap: 200 })
let uploadContext = {}
let uploadController
let uploadSequence = 0
const folderTrail = computed(() => knowledgeFolderTrail(folders.value, selection.folderId))
const uploadFolderName = computed(() => folderTrail.value.map(folder => folder.name).join(' / ') || '全部文档')
const uploadStatus = status => ({
  queued: '等待上传',
  uploading: '上传中',
  indexing: '索引中',
  done: '完成',
  failed: '失败',
})[status]

function openUpload() {
  uploadContext = { dbId: selection.dbId, folderId: selection.folderId }
  uploadItems.value = []
  uploadError.value = ''
  uploadOpen.value = true
}

function queueFile(file) {
  try {
    validateChunkParams(uploadParams)
  } catch (error) {
    uploadError.value = message(error)
    return false
  }
  uploadError.value = ''
  uploadItems.value.push({
    uid: ++uploadSequence,
    file,
    status: 'queued',
    progress: 0,
    error: '',
    uploadId: null,
  })
  queueMicrotask(processUploadQueue)
  return false
}

function retryUpload(item) {
  item.status = 'queued'
  item.error = ''
  processUploadQueue()
}

async function processUploadQueue() {
  if (uploading.value || disposed) return
  let params
  try {
    params = validateChunkParams(uploadParams)
  } catch (error) {
    uploadError.value = message(error)
    return
  }

  uploading.value = true
  uploadController = new AbortController()
  const context = { ...uploadContext }
  try {
    for (const item of uploadItems.value) {
      if (item.status !== 'queued' || disposed) continue
      try {
        if (!item.uploadId) {
          item.status = 'uploading'
          const result = unwrapKnowledgeResponse(await api.uploadKnowledgeFile(
            context.dbId,
            item.file,
            context.folderId,
            event => { item.progress = event.total ? Math.round(event.loaded / event.total * 100) : 0 },
            uploadController.signal,
          ))
          if (!result.upload_id) throw new Error('服务器未返回有效的上传标识')
          item.uploadId = result.upload_id
        }
        item.status = 'indexing'
        item.progress = 100
        unwrapKnowledgeResponse(await api.indexKnowledgeDocuments(context.dbId, [item.uploadId], params, uploadController.signal))
        item.status = 'done'
      } catch (error) {
        item.status = 'failed'
        item.error = message(error)
      }
    }
  } finally {
    uploading.value = false
    if (!disposed && selection.dbId === context.dbId && activeView.value === 'documents') await loadDocuments()
  }
}

const queryLoading = ref(false)
const queryError = ref('')
const queryElapsed = ref(0)
let queryController

function clearQuery() {
  queryController?.abort()
  queryLoading.value = false
  queryError.value = ''
  queryElapsed.value = 0
  selection.queryResult = null
}

async function runQuery({ text, target, mode }) {
  if (queryLoading.value || !selection.dbId) return
  const current = generation
  const start = performance.now()
  queryLoading.value = true
  queryError.value = ''
  selection.queryResult = null
  queryController = new AbortController()
  try {
    const query = target === 'graph' ? api.queryKnowledgeGraph : api.queryKnowledgeBase
    const response = await query(selection.dbId, text, {
      mode,
      top_k: 8,
      only_need_context: target !== 'graph',
    }, queryController.signal)
    if (current === generation && !disposed) {
      selection.queryResult = normalizeKnowledgeQuery(response)
      queryElapsed.value = Math.round(performance.now() - start)
    }
  } catch (error) {
    if (current === generation && !disposed) queryError.value = message(error)
  } finally {
    if (current === generation && !disposed) queryLoading.value = false
  }
}

const graphLabels = ref([])
const graphLoading = ref(false)
const graphError = ref('')
const graphData = ref(normalizeKnowledgeGraph({ nodes: [], edges: [] }))
const defaultGraphParams = { node_label: '*', max_depth: 2, max_nodes: 100 }

async function loadGraph(params = defaultGraphParams) {
  if (!selection.dbId || graphLoading.value) return
  const current = generation
  graphLoading.value = true
  graphError.value = ''
  try {
    const data = normalizeKnowledgeGraph(await api.getKnowledgeSubgraph(selection.dbId, params))
    if (current === generation && !disposed) graphData.value = data
  } catch (error) {
    if (current === generation && !disposed) graphError.value = message(error)
  } finally {
    if (current === generation && !disposed) graphLoading.value = false
  }
}

async function loadGraphWorkspace() {
  if (!selection.dbId) return
  const current = generation
  graphError.value = ''
  try {
    const data = unwrapKnowledgeResponse(await api.listKnowledgeGraphLabels(selection.dbId))
    if (current !== generation || disposed) return
    graphLabels.value = (data.labels || []).map(String)
  } catch (error) {
    if (current === generation && !disposed) graphError.value = message(error)
    return
  }
  if (current === generation && !disposed) await loadGraph(defaultGraphParams)
}

watch(
  () => [route.query.dbId, route.query.view],
  () => { syncRoute() },
)

onMounted(async () => {
  await loadDatabases()
  await syncRoute()
})

onBeforeUnmount(() => {
  disposed = true
  generation += 1
  listGeneration += 1
  previewGeneration += 1
  uploadController?.abort()
  queryController?.abort()
})
</script>

<style scoped lang="scss">
.ai-knowledge-center {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
  color: var(--color-text);
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}

.knowledge-route-state {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
}

.section-alert {
  margin-bottom: 14px;
}

.form-hint {
  margin: 8px 0 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.upload-results {
  max-height: 300px;
  overflow: auto;
}

.upload-result {
  margin-top: 16px;
}

.upload-result > div:first-child {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.upload-name {
  min-width: 0;
  overflow-wrap: anywhere;
}

.inline-error {
  margin-bottom: 4px;
  color: var(--color-error, #d4380d);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.document-preview {
  min-height: 120px;
  max-height: 65vh;
  overflow: auto;
  color: var(--color-text);
  font: inherit;
  line-height: 1.8;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
</style>
