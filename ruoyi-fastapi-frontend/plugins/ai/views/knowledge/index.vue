<template>
  <main class="app-container ai-knowledge-center">
    <WorkspaceToolbar title="知识库" subtitle="管理文档、验证检索效果与探索知识图谱">
      <template #actions>
        <a-button :disabled="busy" :loading="listLoading || detailLoading" aria-label="刷新知识库" @click="refresh"><ReloadOutlined /></a-button>
        <a-button v-if="can('add') && !selectedDb" type="primary" @click="openCreate"><PlusOutlined />创建知识库</a-button>
      </template>
    </WorkspaceToolbar>
    <section class="knowledge-workspace">
      <WorkspaceState v-if="!can('list')" kind="forbidden" title="暂无知识库管理权限" />
      <template v-else-if="!selectedDb">
        <div class="knowledge-filters"><a-input-search v-model:value="search" placeholder="搜索知识库名称或描述" allow-clear aria-label="搜索知识库" /></div>
        <WorkspaceState v-if="listLoading" kind="loading" />
        <WorkspaceState v-else-if="listError" kind="error" :message="listError" @retry="loadDatabases" />
        <WorkspaceState v-else-if="!filteredDatabases.length" kind="empty" :title="search ? '没有匹配的知识库' : '还没有知识库'" :message="search ? '尝试其他名称或描述' : '创建知识库后，可上传文档并进行检索'" />
        <a-list v-else class="database-list" :data-source="filteredDatabases">
          <template #renderItem="{ item }">
            <a-list-item>
              <div class="database-info">
                <a-button type="link" class="database-name" :disabled="!can('query')" @click="selectDatabase(item)"><DatabaseOutlined />{{ item.name }}</a-button>
                <p>{{ item.description || '暂无描述' }}</p>
                <div class="database-meta"><a-tag>{{ typeLabel(item.kb_type) }}</a-tag><a-tag v-if="item.enable_knowledge_graph" color="blue">含知识图谱</a-tag><span>{{ formatTime(item.created_at) }}</span></div>
              </div>
              <template #actions>
                <a-button v-if="can('edit')" type="text" @click="openEdit('renameDb', item)">编辑</a-button>
                <a-button v-if="can('remove')" type="text" danger @click="askDelete('database', item)">删除</a-button>
              </template>
            </a-list-item>
          </template>
        </a-list>
      </template>
      <template v-else>
        <div class="knowledge-detail-heading">
          <a-button :disabled="busy" @click="selectDatabase(null)"><ArrowLeftOutlined />全部知识库</a-button>
          <div class="knowledge-detail-title"><h2>{{ selectedDb.name }}</h2><p>{{ selectedDb.description || '暂无描述' }}</p></div>
          <a-tag>{{ typeLabel(selectedDb.kb_type) }}</a-tag>
        </div>
        <a-tabs v-model:active-key="selection.tab" class="workspace-tabs" @change="onTabChange">
          <a-tab-pane key="documents" tab="文档管理">
            <div class="document-toolbar">
              <div class="document-filters">
                <a-input-search v-model:value="documentSearch" placeholder="搜索当前目录文件" allow-clear aria-label="搜索文档" />
                <a-select v-model:value="documentKind" aria-label="筛选文件类型" :options="[{ value: 'all', label: '全部类型' }, { value: 'folders', label: '仅文件夹' }, { value: 'files', label: '仅文件' }]" />
                <a-select v-model:value="documentTime" aria-label="筛选上传时间" :options="[{ value: '', label: '全部时间' }, { value: 'today', label: '今天' }, { value: 'week', label: '最近 7 天' }, { value: 'month', label: '最近 30 天' }]" />
              </div>
              <div class="document-actions"><a-button v-if="can('edit')" :disabled="busy" @click="openEdit('createFolder')"><FolderAddOutlined />新建文件夹</a-button><a-button v-if="can('edit')" type="primary" :disabled="busy" @click="openUpload"><UploadOutlined />上传文档</a-button></div>
            </div>
            <div class="document-path"><a-breadcrumb><a-breadcrumb-item><a-button type="link" :disabled="busy" @click="selection.folderId = null">全部文档</a-button></a-breadcrumb-item><a-breadcrumb-item v-for="folder in folderTrail" :key="folder.id"><a-button type="link" :disabled="busy" @click="selection.folderId = folder.id">{{ folder.name }}</a-button></a-breadcrumb-item></a-breadcrumb><span>{{ folders.length }} 个文件夹 · {{ documents.length }} 个文档</span></div>
            <WorkspaceState v-if="detailLoading" kind="loading" />
            <WorkspaceState v-else-if="detailError" kind="error" :message="detailError" @retry="loadDocuments" />
            <a-table v-else :columns="documentColumns" :data-source="documentRows" row-key="rowKey" size="middle" :scroll="{ x: 760 }" :pagination="{ pageSize: 15, hideOnSinglePage: true, showSizeChanger: false }" :locale="{ emptyText: '当前目录暂无文档' }">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'name'"><a-button type="link" class="document-name" :disabled="busy" @click="record.isFolder ? selection.folderId = record.id : openPreview(record)"><FolderOutlined v-if="record.isFolder" /><FileTextOutlined v-else />{{ record.file_name || record.name }}</a-button></template>
                <template v-else-if="column.key === 'status'"><span v-if="record.isFolder">文件夹</span><a-tag v-else :color="['failed', 'error'].includes(record.status) ? 'error' : ['processing', 'pending'].includes(record.status) ? 'processing' : undefined">{{ documentStatus(record.status) }}</a-tag></template>
                <template v-else-if="column.key === 'size'">{{ record.isFolder ? '—' : formatSize(record.file_size) }}</template>
                <template v-else-if="column.key === 'chunks'">{{ record.isFolder ? '—' : `${record.chunk_size || '—'} / ${record.chunk_overlap ?? '—'}` }}</template>
                <template v-else-if="column.key === 'time'">{{ formatTime(record.created_at) }}</template>
                <template v-else-if="column.key === 'actions'"><a-space :size="0"><a-button v-if="can('edit')" type="link" size="small" :disabled="busy" @click="openEdit(record.isFolder ? 'renameFolder' : 'rechunk', record)">{{ record.isFolder ? '重命名' : '重新切片' }}</a-button><a-button v-if="can('remove')" type="link" size="small" danger :disabled="busy" @click="askDelete(record.isFolder ? 'folder' : 'document', record)">删除</a-button></a-space></template>
              </template>
            </a-table>
          </a-tab-pane>
          <a-tab-pane key="query" tab="检索评估">
            <div class="query-toolbar"><a-input v-model:value="queryText" placeholder="输入需要检索的问题" :maxlength="8000" aria-label="检索问题" @press-enter="runQuery" /><a-select v-model:value="queryTarget" aria-label="检索目标" :options="[{ value: 'kb', label: '知识库' }, { value: 'graph', label: '知识图谱', disabled: !selectedDb.enable_knowledge_graph }]" /><a-select v-model:value="queryMode" aria-label="检索模式" :options="queryModes" /><a-button type="primary" :loading="queryLoading" :disabled="!queryText.trim()" @click="runQuery">检索</a-button></div>
            <a-alert v-if="queryError" class="section-alert" type="error" :message="queryError" show-icon />
            <template v-if="selection.queryResult">
              <div class="query-metrics"><span>{{ selection.queryResult.items.length ? `${selection.queryResult.items.length} 条结果` : '检索回答' }}</span><span>{{ queryChars }} 字符</span><span>耗时 {{ queryElapsed }} ms</span><span v-if="queryAverage != null">平均相似度 {{ (queryAverage * 100).toFixed(1) }}%</span></div>
              <pre v-if="selection.queryResult.answer" class="query-answer">{{ selection.queryResult.answer }}</pre>
              <article v-for="(item, index) in selection.queryResult.items" :key="`${item.id}-${index}`" class="query-passage"><div class="query-source"><span>#{{ index + 1 }} · {{ item.source }}</span><span v-if="item.score != null">相似度 {{ (item.score * 100).toFixed(1) }}%</span></div><p>{{ item.content }}</p></article>
              <WorkspaceState v-if="!selection.queryResult.answer && !selection.queryResult.items.length" kind="empty" title="没有检索到相关内容" />
            </template>
            <WorkspaceState v-else-if="!queryLoading && !queryError" kind="empty" title="验证文档检索效果" message="输入问题，查看命中的文档片段与相关性" />
          </a-tab-pane>
          <a-tab-pane v-if="selectedDb.enable_knowledge_graph" key="graph" tab="知识图谱">
            <div class="graph-toolbar"><a-select v-model:value="graphForm.node_label" show-search option-filter-prop="label" :options="graphLabelOptions" aria-label="图谱实体" /><label>深度 <a-input-number v-model:value="graphForm.max_depth" :min="1" :max="5" :precision="0" aria-label="图谱深度" /></label><label>节点上限 <a-input-number v-model:value="graphForm.max_nodes" :min="1" :max="200" :precision="0" aria-label="图谱节点上限" /></label><a-button type="primary" :loading="graphLoading" @click="loadGraph">加载图谱</a-button></div>
            <a-alert v-if="graphError" class="section-alert" type="error" :message="graphError" show-icon />
            <a-alert v-if="graphData.truncated" class="section-alert" type="info" message="当前展示部分节点。可选择具体实体或调整深度继续探索。" show-icon />
            <div v-if="graphData.nodes.length" class="graph-summary">当前子图：{{ graphData.nodes.length }} 个节点 · {{ graphData.links.length }} 个关系 · {{ graphData.categories.length }} 种实体类型</div>
            <KnowledgeGraph v-if="graphData.nodes.length" :graph="graphData" />
            <WorkspaceState v-else :kind="graphLoading ? 'loading' : 'empty'" :title="graphLoading ? '正在加载图谱' : '暂无图谱节点'" />
          </a-tab-pane>
        </a-tabs>
      </template>
    </section>

    <a-modal v-model:open="editor.open" :title="editorTitle" centered width="min(580px, calc(100vw - 32px))" :confirm-loading="actionBusy" :mask-closable="false" :closable="!actionBusy" :keyboard="!actionBusy" :ok-button-props="{ disabled: editorLoading }" @ok="saveEditor">
      <a-alert v-if="editorError" class="section-alert" type="error" :message="editorError" show-icon />
      <a-spin :spinning="editorLoading">
        <a-form layout="vertical" :disabled="actionBusy || editorLoading">
          <template v-if="editor.kind === 'createDb'">
            <a-form-item label="知识库名称" required><a-input v-model:value="databaseForm.database_name" aria-label="知识库名称" :maxlength="100" /></a-form-item>
            <a-form-item label="描述"><a-textarea v-model:value="databaseForm.description" aria-label="知识库描述" :rows="2" :maxlength="500" /></a-form-item>
            <a-form-item label="存储类型" required><a-select v-model:value="databaseForm.kb_type" aria-label="存储类型" :options="storageTypes" @change="onStorageChange" /></a-form-item>
            <a-alert v-if="!storageTypes.some(item => item.available) && !editorLoading" class="section-alert" type="warning" message="暂无可用存储类型，请联系管理员完成知识库配置。" show-icon />
            <a-form-item label="Embedding 模型" required><a-select v-model:value="databaseForm.embed_model_id" aria-label="Embedding 模型" :options="embeddingOptions" show-search option-filter-prop="label" placeholder="选择已发布的 Embedding 模型" /></a-form-item>
            <a-form-item><a-checkbox v-model:checked="databaseForm.enable_knowledge_graph" :disabled="!graphAvailable || databaseForm.kb_type === 'lightrag'">同时构建知识图谱</a-checkbox><p v-if="!graphAvailable" class="form-hint">知识图谱服务尚未配置。</p></a-form-item>
            <a-form-item v-if="databaseForm.enable_knowledge_graph" label="图谱对话模型" required><a-select v-model:value="databaseForm.llm_model_id" aria-label="图谱对话模型" :options="chatOptions" show-search option-filter-prop="label" placeholder="选择已发布的对话模型" /></a-form-item>
          </template>
          <template v-else-if="editor.kind === 'rechunk'"><p>为「{{ editor.item?.file_name }}」重新生成切片。</p><ChunkFields v-model:size="chunkForm.chunk_size" v-model:overlap="chunkForm.chunk_overlap" /></template>
          <template v-else><a-form-item label="名称" required><a-input v-model:value="nameForm.name" aria-label="名称" :maxlength="100" /></a-form-item><a-form-item v-if="editor.kind === 'renameDb'" label="描述"><a-textarea v-model:value="nameForm.description" aria-label="知识库描述" :rows="2" :maxlength="500" /></a-form-item></template>
        </a-form>
      </a-spin>
    </a-modal>

    <a-modal v-model:open="deletion.open" title="确认删除" centered :confirm-loading="actionBusy" :mask-closable="false" :closable="!actionBusy" :keyboard="!actionBusy" ok-text="删除" :ok-button-props="{ danger: true }" @ok="confirmDelete">
      <a-alert v-if="deleteError" class="section-alert" type="error" :message="deleteError" show-icon />
      <p>确定删除「{{ deletion.item?.name || deletion.item?.file_name }}」？</p><p class="form-hint">{{ deletion.kind === 'folder' ? '文件夹及其中的子目录和文档将一并删除。此操作无法撤销。' : deletion.kind === 'database' ? '请先清空库内文档。删除后无法恢复知识库。' : '文档及其索引将被删除，此操作无法撤销。' }}</p>
    </a-modal>

    <a-modal v-model:open="uploadOpen" title="上传文档" centered width="min(640px, calc(100vw - 32px))" :footer="null" :mask-closable="!uploading" :closable="!uploading" :keyboard="!uploading">
      <p class="form-hint">{{ selectedDb?.name }} / {{ uploadFolderName }} · 支持 PDF、Word、TXT、Markdown 等文档</p>
      <a-alert v-if="uploadError" class="section-alert" type="error" :message="uploadError" show-icon />
      <a-form layout="vertical" :disabled="uploading"><ChunkFields v-model:size="uploadParams.chunk_size" v-model:overlap="uploadParams.chunk_overlap" /></a-form>
      <a-upload-dragger multiple :before-upload="queueFile" :show-upload-list="false" :disabled="uploading" accept=".pdf,.doc,.docx,.txt,.md,.markdown,.csv,.xlsx,.xls,.ppt,.pptx,.html"><p class="ant-upload-drag-icon"><InboxOutlined /></p><p>点击选择或拖拽文档到此处</p></a-upload-dragger>
      <div class="upload-results" aria-live="polite"><div v-for="item in uploadItems" :key="item.uid" class="upload-result"><div><span class="upload-name">{{ item.file.name }}</span><a-tag :color="item.status === 'failed' ? 'error' : item.status === 'done' ? 'success' : 'processing'">{{ uploadStatus(item.status) }}</a-tag></div><a-progress :percent="item.progress" :status="item.status === 'failed' ? 'exception' : item.status === 'done' ? 'success' : 'active'" size="small" /><p v-if="item.error" class="inline-error">{{ item.error }}</p><a-button v-if="item.status === 'failed' && !uploading" type="link" @click="retryUpload(item)">重试</a-button></div></div>
    </a-modal>

    <a-modal v-model:open="preview.open" :title="preview.name" centered width="min(900px, calc(100vw - 32px))" :footer="null"><a-spin :spinning="preview.loading"><a-alert v-if="preview.error" type="error" :message="preview.error" show-icon /><pre v-else class="document-preview">{{ preview.content || (preview.loading ? '正在读取文档…' : '文档暂无可预览内容') }}</pre></a-spin></a-modal>
  </main>
</template>

<script setup name="AiKnowledge">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ArrowLeftOutlined, DatabaseOutlined, FileTextOutlined, FolderAddOutlined, FolderOutlined, InboxOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons-vue'
import useUserStore from '@/store/modules/user'
import feedback from '@/plugins/antdFeedback'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import KnowledgeGraph from './components/KnowledgeGraph.vue'
import ChunkFields from './components/ChunkFields.vue'
import * as api from '../../api/knowledge.js'
import { buildKnowledgeDatabasePayload, filterKnowledgeDocuments, knowledgeFolderTrail, normalizeKnowledgeDatabases, normalizeKnowledgeGraph, normalizeKnowledgeQuery, normalizeKnowledgeTypes, resetKnowledgeSelection, unwrapKnowledgeResponse, validateChunkParams } from '../../utils/knowledge.js'

const userStore = useUserStore()
const can = action => userStore.permissions?.includes('*:*:*') || userStore.permissions?.includes(`ai:knowledge:${action}`)
const message = error => normalizeErrorMessage(error, '操作失败，请重试')
const databases = ref([]), search = ref(''), listLoading = ref(false), listError = ref('')
const selection = reactive(resetKnowledgeSelection())
const selectedDb = computed(() => databases.value.find(db => db.db_id === selection.dbId))
const filteredDatabases = computed(() => databases.value.filter(db => `${db.name} ${db.description}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const folders = ref([]), documents = ref([]), detailLoading = ref(false), detailError = ref('')
const documentSearch = ref(''), documentTime = ref(''), documentKind = ref('all')
const folderTrail = computed(() => knowledgeFolderTrail(folders.value, selection.folderId))
const documentRows = computed(() => [
  ...(documentKind.value === 'files' ? [] : folders.value.filter(folder => (folder.parent_id || null) === selection.folderId && (!documentSearch.value || folder.name.toLowerCase().includes(documentSearch.value.toLowerCase()))).map(folder => ({ ...folder, rowKey: `folder-${folder.id}`, isFolder: true }))),
  ...(documentKind.value === 'folders' ? [] : filterKnowledgeDocuments(documents.value, { folderId: selection.folderId, search: documentSearch.value, time: documentTime.value }).map(doc => ({ ...doc, rowKey: `doc-${doc.id}` }))),
])
const documentColumns = [{ title: '文件名', key: 'name', width: 250 }, { title: '状态', key: 'status', width: 95 }, { title: '大小', key: 'size', width: 90 }, { title: '切片 / 重叠', key: 'chunks', width: 115 }, { title: '上传时间', key: 'time', width: 160 }, { title: '操作', key: 'actions', width: 155 }]
const actionBusy = ref(false), uploading = ref(false)
const busy = computed(() => actionBusy.value || uploading.value)
let generation = 0, listGeneration = 0, previewGeneration = 0, disposed = false

const typeLabel = type => ({ chroma: 'Chroma', milvus: 'Milvus', lightrag: 'LightRAG' })[type] || type || '知识库'
const documentStatus = status => ({ done: '已索引', completed: '已索引', success: '已索引', processed: '已索引', pending: '等待处理', processing: '处理中', failed: '失败', error: '失败' })[status] || status || '已索引'
const uploadStatus = status => ({ queued: '等待上传', uploading: '上传中', indexing: '索引中', done: '完成', failed: '失败' })[status]
function formatTime(value) { if (!value) return '—'; const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value); return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false }) }
function formatSize(value) { const size = Number(value || 0); return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB` }

async function loadDatabases() {
  if (!can('list')) return
  const requestId = ++listGeneration
  listLoading.value = true; listError.value = ''
  try { const rows = normalizeKnowledgeDatabases(await api.listKnowledgeBases()); if (requestId === listGeneration && !disposed) databases.value = rows }
  catch (error) { if (requestId === listGeneration && !disposed) listError.value = message(error) }
  finally { if (requestId === listGeneration && !disposed) listLoading.value = false }
}
function selectDatabase(database) {
  if (busy.value) return
  generation++; queryController?.abort()
  Object.assign(selection, resetKnowledgeSelection(database?.db_id || null))
  folders.value = []; documents.value = []; detailError.value = ''; documentSearch.value = ''; documentTime.value = ''; documentKind.value = 'all'
  queryText.value = ''; queryError.value = ''; queryLoading.value = false; queryTarget.value = 'kb'
  graphData.value = normalizeKnowledgeGraph({ nodes: [], edges: [] }); graphLabels.value = []; graphError.value = ''; graphLoading.value = false; graphForm.node_label = '*'
  if (database) loadDocuments()
}
async function loadDocuments() {
  if (!selection.dbId) return
  const current = generation, dbId = selection.dbId
  detailLoading.value = true; detailError.value = ''
  try {
    const [folderResponse, documentResponse] = await Promise.all([api.listKnowledgeFolders(dbId), api.listKnowledgeDocuments(dbId)])
    if (current !== generation || disposed) return
    folders.value = unwrapKnowledgeResponse(folderResponse).folders || []
    documents.value = unwrapKnowledgeResponse(documentResponse).documents || []
    if (selection.folderId && !folders.value.some(folder => folder.id === selection.folderId)) selection.folderId = null
  } catch (error) { if (current === generation && !disposed) detailError.value = message(error) }
  finally { if (current === generation && !disposed) detailLoading.value = false }
}
function refresh() { if (selection.dbId) { if (selection.tab === 'graph') loadGraph(); else loadDocuments() } else loadDatabases() }

const editor = reactive({ open: false, kind: '', item: null }), editorError = ref(''), editorLoading = ref(false)
const storageTypes = ref([]), embeddingModels = ref([]), chatModels = ref([])
const databaseForm = reactive({ database_name: '', description: '', kb_type: '', embed_model_id: undefined, llm_model_id: undefined, enable_knowledge_graph: false })
const nameForm = reactive({ name: '', description: '' }), chunkForm = reactive({ chunk_size: 1000, chunk_overlap: 200 })
const modelOptions = models => models.map(model => ({ value: model.model_id, label: model.display_name || model.model_code || model.model_id }))
const embeddingOptions = computed(() => modelOptions(embeddingModels.value)), chatOptions = computed(() => modelOptions(chatModels.value))
const graphAvailable = computed(() => storageTypes.value.some(type => type.value === 'lightrag' && type.available))
const editorTitle = computed(() => ({ createDb: '创建知识库', renameDb: '编辑知识库', createFolder: '新建文件夹', renameFolder: '重命名文件夹', rechunk: '重新切片' })[editor.kind])
async function openCreate() {
  editor.kind = 'createDb'; editor.item = null; editor.open = true; editorError.value = ''; editorLoading.value = true
  storageTypes.value = []; embeddingModels.value = []; chatModels.value = []
  Object.assign(databaseForm, { database_name: '', description: '', kb_type: '', embed_model_id: undefined, llm_model_id: undefined, enable_knowledge_graph: false })
  try {
    const [types, embeddings, chats] = await Promise.all([api.listKnowledgeTypes(), api.listKnowledgeModels('embedding'), api.listKnowledgeModels('chat')])
    if (disposed) return
    storageTypes.value = normalizeKnowledgeTypes(types); embeddingModels.value = unwrapKnowledgeResponse(embeddings).models || []; chatModels.value = unwrapKnowledgeResponse(chats).models || []
    databaseForm.kb_type = storageTypes.value.find(type => type.available)?.value || ''; onStorageChange()
  } catch (error) { if (!disposed) editorError.value = message(error) }
  finally { if (!disposed) editorLoading.value = false }
}
function onStorageChange() { if (databaseForm.kb_type === 'lightrag') databaseForm.enable_knowledge_graph = true; else if (!graphAvailable.value) databaseForm.enable_knowledge_graph = false }
function openEdit(kind, item = null) {
  editor.kind = kind; editor.item = item; editor.open = true; editorError.value = ''
  nameForm.name = item?.name || ''; nameForm.description = item?.description || ''
  chunkForm.chunk_size = item?.chunk_size || 1000; chunkForm.chunk_overlap = item?.chunk_overlap ?? 200
}
async function saveEditor() {
  if (actionBusy.value || editorLoading.value) return
  editorError.value = ''; actionBusy.value = true
  try {
    let result
    if (editor.kind === 'createDb') {
      if (!storageTypes.value.some(type => type.value === databaseForm.kb_type && type.available)) throw new Error('请选择可用的存储类型')
      result = await api.createKnowledgeBase(buildKnowledgeDatabasePayload(databaseForm))
    } else if (editor.kind === 'rechunk') result = await api.rechunkKnowledgeDocument(selection.dbId, editor.item.id, validateChunkParams(chunkForm))
    else {
      const name = nameForm.name.trim()
      if (!name) throw new Error('请输入名称')
      if (editor.kind === 'renameDb') result = await api.updateKnowledgeBase(editor.item.db_id, { name, description: nameForm.description.trim() })
      else if (editor.kind === 'createFolder') result = await api.createKnowledgeFolder({ name, db_id: selection.dbId, ...(selection.folderId ? { parent_id: selection.folderId } : {}) })
      else result = await api.renameKnowledgeFolder(selection.dbId, editor.item.id, name)
    }
    unwrapKnowledgeResponse(result); editor.open = false; feedback.msgSuccess('已保存')
    if (['createDb', 'renameDb'].includes(editor.kind)) await loadDatabases(); else await loadDocuments()
  } catch (error) { editorError.value = message(error) }
  finally { actionBusy.value = false }
}

const deletion = reactive({ open: false, kind: '', item: null }), deleteError = ref('')
function askDelete(kind, item) { deletion.open = true; deletion.kind = kind; deletion.item = item; deleteError.value = '' }
async function confirmDelete() {
  if (actionBusy.value) return
  actionBusy.value = true; deleteError.value = ''
  try {
    const item = deletion.item
    let response
    if (deletion.kind === 'database') response = await api.deleteKnowledgeBase(item.db_id)
    else if (deletion.kind === 'folder') response = await api.deleteKnowledgeFolder(selection.dbId, item.id)
    else response = await api.deleteKnowledgeDocument(selection.dbId, item.id)
    unwrapKnowledgeResponse(response); deletion.open = false; feedback.msgSuccess('已删除')
    if (deletion.kind === 'database') await loadDatabases(); else await loadDocuments()
  } catch (error) { deleteError.value = message(error) }
  finally { actionBusy.value = false }
}

const preview = reactive({ open: false, name: '', loading: false, error: '', content: '' })
async function openPreview(document) {
  const requestId = ++previewGeneration
  Object.assign(preview, { open: true, name: document.file_name, loading: true, error: '', content: '' })
  try { const data = unwrapKnowledgeResponse(await api.previewKnowledgeDocument(selection.dbId, document.id)); if (requestId === previewGeneration && !disposed) preview.content = String(data.content || '') }
  catch (error) { if (requestId === previewGeneration && !disposed) preview.error = message(error) }
  finally { if (requestId === previewGeneration && !disposed) preview.loading = false }
}

const uploadOpen = ref(false), uploadItems = ref([]), uploadError = ref('')
const uploadParams = reactive({ chunk_size: 1000, chunk_overlap: 200 })
let uploadContext = {}, uploadController, uploadSequence = 0
const uploadFolderName = computed(() => folderTrail.value.map(folder => folder.name).join(' / ') || '全部文档')
function openUpload() { uploadContext = { dbId: selection.dbId, folderId: selection.folderId }; uploadItems.value = []; uploadError.value = ''; uploadOpen.value = true }
function queueFile(file) {
  try { validateChunkParams(uploadParams) } catch (error) { uploadError.value = message(error); return false }
  uploadError.value = ''
  uploadItems.value.push({ uid: ++uploadSequence, file, status: 'queued', progress: 0, error: '', uploadId: null })
  queueMicrotask(processUploadQueue)
  return false
}
function retryUpload(item) { item.status = 'queued'; item.error = ''; processUploadQueue() }
async function processUploadQueue() {
  if (uploading.value || disposed) return
  let params
  try { params = validateChunkParams(uploadParams) } catch (error) { uploadError.value = message(error); return }
  uploading.value = true; uploadController = new AbortController()
  const context = { ...uploadContext }
  try {
    for (const item of uploadItems.value) {
      if (item.status !== 'queued' || disposed) continue
      try {
        if (!item.uploadId) {
          item.status = 'uploading'
          const result = unwrapKnowledgeResponse(await api.uploadKnowledgeFile(context.dbId, item.file, context.folderId, event => { item.progress = event.total ? Math.round(event.loaded / event.total * 100) : 0 }, uploadController.signal))
          if (!result.upload_id) throw new Error('服务器未返回有效的上传标识')
          item.uploadId = result.upload_id
        }
        item.status = 'indexing'; item.progress = 100
        unwrapKnowledgeResponse(await api.indexKnowledgeDocuments(context.dbId, [item.uploadId], params, uploadController.signal))
        item.status = 'done'
      } catch (error) { item.status = 'failed'; item.error = message(error) }
    }
  } finally { uploading.value = false; if (!disposed && selection.dbId === context.dbId) await loadDocuments() }
}

const queryText = ref(''), queryMode = ref('mix'), queryTarget = ref('kb'), queryLoading = ref(false), queryError = ref(''), queryElapsed = ref(0)
const queryModes = [{ value: 'mix', label: '混合检索' }, { value: 'local', label: '段落检索' }, { value: 'global', label: '关联检索' }, { value: 'hybrid', label: '综合检索' }, { value: 'naive', label: '基础检索' }]
let queryController
const queryChars = computed(() => (selection.queryResult?.answer || '').length + (selection.queryResult?.items || []).reduce((count, item) => count + item.content.length, 0))
const queryAverage = computed(() => { const scores = (selection.queryResult?.items || []).map(item => item.score).filter(score => score != null); return scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null })
async function runQuery() {
  if (queryLoading.value || !queryText.value.trim() || !selection.dbId) return
  const current = generation, start = performance.now()
  queryLoading.value = true; queryError.value = ''; selection.queryResult = null; queryController = new AbortController()
  try {
    const query = queryTarget.value === 'graph' ? api.queryKnowledgeGraph : api.queryKnowledgeBase
    const result = await query(selection.dbId, queryText.value.trim(), { mode: queryMode.value, top_k: 8, only_need_context: queryTarget.value !== 'graph' }, queryController.signal)
    if (current === generation && !disposed) { selection.queryResult = normalizeKnowledgeQuery(result); queryElapsed.value = Math.round(performance.now() - start) }
  } catch (error) { if (current === generation && !disposed) queryError.value = message(error) }
  finally { if (current === generation && !disposed) queryLoading.value = false }
}

const graphForm = reactive({ node_label: '*', max_depth: 2, max_nodes: 100 }), graphLabels = ref([]), graphLoading = ref(false), graphError = ref('')
const graphData = ref(normalizeKnowledgeGraph({ nodes: [], edges: [] }))
const graphLabelOptions = computed(() => [{ value: '*', label: '全部实体' }, ...graphLabels.value.filter(label => label !== '*').map(label => ({ value: label, label }))])
async function onTabChange(tab) {
  if (tab !== 'graph' || !selection.dbId) return
  const current = generation
  try { const data = unwrapKnowledgeResponse(await api.listKnowledgeGraphLabels(selection.dbId)); if (current === generation && !disposed) graphLabels.value = (data.labels || []).map(String) }
  catch (error) { if (current === generation && !disposed) graphError.value = message(error); return }
  if (current === generation && !disposed) loadGraph()
}
async function loadGraph() {
  if (!selection.dbId || graphLoading.value) return
  const current = generation
  graphLoading.value = true; graphError.value = ''
  try { const data = normalizeKnowledgeGraph(await api.getKnowledgeSubgraph(selection.dbId, graphForm)); if (current === generation && !disposed) graphData.value = data }
  catch (error) { if (current === generation && !disposed) graphError.value = message(error) }
  finally { if (current === generation && !disposed) graphLoading.value = false }
}

onMounted(loadDatabases)
onBeforeUnmount(() => { disposed = true; generation++; listGeneration++; previewGeneration++; uploadController?.abort(); queryController?.abort() })
</script>

<style scoped>
.ai-knowledge-center { height: 100%; min-height: 0; min-width: 0; display: flex; flex-direction: column; color: var(--color-text); background: var(--bg-container); }
.knowledge-workspace { flex: 1; min-height: 0; min-width: 0; overflow: auto; padding: 18px 24px 24px; }
.knowledge-filters { max-width: 360px; margin-bottom: 16px; }
.database-list :deep(.ant-list-item) { align-items: center; padding: 18px 0; gap: 12px; }
.database-info { min-width: 0; }
.database-name { padding: 0; max-width: 100%; height: auto; font-size: 16px; font-weight: 600; white-space: normal; text-align: left; overflow-wrap: anywhere; }
.database-info p { margin: 8px 0; color: var(--color-text-secondary); overflow-wrap: anywhere; }
.database-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; color: var(--color-text-secondary); font-size: 12px; }
.knowledge-detail-heading { display: flex; align-items: center; gap: 16px; margin-bottom: 12px; }
.knowledge-detail-title { min-width: 0; flex: 1; }
.knowledge-detail-title h2 { margin: 0; font-size: 18px; color: var(--color-text); overflow-wrap: anywhere; }
.knowledge-detail-title p { margin: 4px 0 0; color: var(--color-text-secondary); overflow-wrap: anywhere; }
.document-toolbar, .document-filters, .document-actions, .query-toolbar, .graph-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.document-toolbar { justify-content: space-between; margin-bottom: 12px; }
.document-filters { flex: 1; }
.document-filters .ant-input-search { width: 250px; }
.document-filters .ant-select { min-width: 115px; }
.document-path { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; margin-bottom: 12px; gap: 8px; color: var(--color-text-secondary); font-size: 12px; }
.document-path :deep(.ant-btn) { padding: 0; height: auto; }
.document-name { padding: 0; max-width: 250px; height: auto; white-space: normal; text-align: left; overflow-wrap: anywhere; }
.query-toolbar { margin-bottom: 16px; }
.query-toolbar > .ant-input { flex: 1; min-width: 220px; }
.query-toolbar > .ant-select { min-width: 115px; }
.query-metrics, .graph-summary { display: flex; flex-wrap: wrap; gap: 20px; margin: 16px 0; color: var(--color-text-secondary); font-size: 13px; }
.query-passage { padding: 16px 0; border-bottom: 1px solid var(--gray-150); }
.query-source { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; color: var(--color-text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.query-passage p, .query-answer, .document-preview { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.8; color: var(--color-text); }
.query-passage p { margin: 10px 0 0; }
.graph-toolbar { margin-bottom: 16px; }
.graph-toolbar > .ant-select { min-width: 220px; flex: 1; }
.graph-toolbar label { display: inline-flex; gap: 8px; align-items: center; }
.graph-toolbar .ant-input-number { width: 70px; }
.section-alert { margin-bottom: 14px; }
.form-hint { color: var(--color-text-secondary); font-size: 13px; margin: 8px 0 12px; }
.upload-results { max-height: 300px; overflow: auto; }
.upload-result { margin-top: 16px; }
.upload-result > div:first-child { display: flex; justify-content: space-between; gap: 12px; }
.upload-name { overflow-wrap: anywhere; min-width: 0; }
.inline-error { color: var(--color-error, #d4380d); font-size: 13px; overflow-wrap: anywhere; margin-bottom: 4px; }
.document-preview { min-height: 120px; max-height: 65vh; overflow: auto; }
@media (max-width: 760px) { .knowledge-workspace { padding: 14px; } .knowledge-detail-heading { flex-wrap: wrap; gap: 10px; } .knowledge-detail-title { flex-basis: calc(100% - 170px); } .document-filters { flex-basis: 100%; } .document-filters .ant-input-search { width: 100%; } .document-actions { margin-left: auto; } .query-toolbar > .ant-input { flex-basis: 100%; } .database-list :deep(.ant-list-item) { flex-wrap: wrap; } .database-list :deep(.ant-list-item-action) { margin-left: auto; } }
</style>
