<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeftOutlined, InboxOutlined } from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Checkbox as ACheckbox,
  Pagination as APagination,
  Select as ASelect,
  Spin as ASpin,
  Tag as ATag,
  Textarea as ATextarea,
  UploadDragger as AUploadDragger
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { FileAclDrawer, downloadFile, emptyQuery, listFiles, saveBlob } from '../../files/public.ts'
import type { ManagedFile } from '../../files/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { importDocuments } from '../api/index.ts'
import type { KnowledgeDocument, KnowledgeHit } from '../api/index.ts'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import KnowledgeJobs from '../components/KnowledgeJobs.vue'
import KnowledgeOverview from '../components/KnowledgeOverview.vue'
import KnowledgeDocuments from '../components/KnowledgeDocuments.vue'
import KnowledgeQuery from '../components/KnowledgeQuery.vue'
import KnowledgeGraphPage from '../components/KnowledgeGraphPage.vue'
import { chartGraph } from '../graphPresentation.ts'
import KnowledgeAclDrawer from '../components/KnowledgeAclDrawer.vue'
import ChunkFields from '../components/ChunkFields.vue'
import { useKnowledgeWorkspace } from '../composables/workspace.ts'
import { formatSize, validChunks } from '../presentation.ts'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { managedImportStorageKey, parseManagedImportDraft } from '../managedImportDraft.ts'
import type { ManagedImportDraft } from '../managedImportDraft.ts'
const {
  graphWorkspace,
  graphQuery,
  graphTarget,
  submitSelectedQuery,
  models,
  baseId,
  view,
  bases,
  selected,
  folders,
  documents,
  jobs,
  folderId,
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
  uploading,
  uploadDiscarding,
  uploadNotice,
  uploadError,
  uploadItems,
  uploadChunks,
  queryBusy,
  queryError,
  queryResult,
  queryRecoverable,
  preparation,
  elapsed,
  loadList,
  refresh,
  acceptJob,
  navigate,
  back,
  openEditor,
  saveEditor,
  askDelete,
  confirmDelete,
  cancelJob,
  showPreview: workspacePreview,
  showSourceAcl,
  findUsers,
  openUpload,
  closeUpload,
  queueFile,
  submitUpload,
  recoverQuery,
  stopQuery,
  clearQuery
} = useKnowledgeWorkspace()
const queryLoading = computed(() => (graphTarget.value ? graphQuery.busy.value : queryBusy.value))
const queryFailure = computed(() => (graphTarget.value ? graphQuery.error.value : queryError.value))
const queryRecovery = computed(() =>
  graphTarget.value ? graphQuery.recoverable.value : queryRecoverable.value
)
const graphState = graphWorkspace.state
const chart = computed(() => chartGraph(graphWorkspace.graph.value))
const chatOptions = computed(() =>
  models.value
    .filter((model) => model.enabled && model.published && model.model_type === 'chat')
    .map((model) => ({ value: model.id, label: model.name }))
)
const acl = ref<InstanceType<typeof KnowledgeAclDrawer> | null>(null)
const editorTitle = computed(
  () =>
    ({
      create: '创建知识库',
      base: '编辑知识库',
      createFolder: '新建文件夹',
      folder: '重命名文件夹',
      rechunk: '重新切片'
    })[editor.kind]
)
const deletionName = computed(
  () =>
    deletion.item && ('file_name' in deletion.item ? deletion.item.file_name : deletion.item.name)
)
const route = useRoute(),
  router = useRouter()
watch(
  () => [route.query.action, baseId.value],
  () => {
    if (route.query.action !== 'create' || baseId.value) return
    openEditor('create')
    const query = { ...route.query }
    delete query.action
    void router.replace({ path: route.path, query, hash: route.hash })
  },
  { immediate: true }
)
const previewFile = ref<{ id: string; name: string } | null>(null)
const downloading = ref(false)
const downloadScope = new RequestScope()
async function showPreview(doc: KnowledgeDocument | KnowledgeHit): Promise<void> {
  previewFile.value = { id: doc.file_id, name: 'file_name' in doc ? doc.file_name : doc.title }
  await workspacePreview(doc)
}
async function downloadOriginal(file = previewFile.value): Promise<void> {
  if (!file || downloading.value) return
  const ticket = downloadScope.replace()
  downloading.value = true
  try {
    const blob = await downloadFile(file.id, ticket.signal)
    if (ticket.isCurrent()) saveBlob(blob, file.name)
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      actionError.value = errorMessage(reason)
      if (preview.open) preview.error = errorMessage(reason)
      if (
        graphTarget.value &&
        reason instanceof ApiError &&
        [401, 403, 404].includes(reason.status)
      )
        graphQuery.denyProtectedResults('来源权限已变化，图谱结果已清除，请恢复查看后再使用')
    }
  } finally {
    if (ticket.isCurrent()) downloading.value = false
  }
}
const importScope = new RequestScope()
const managedImport = reactive({
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
let pendingImport: ManagedImportDraft | null = null
const importDraftVersion = ref(0)
const importRecoverable = computed(() => {
  void importDraftVersion.value
  if (!identity.value?.id || !baseId.value || actionBusy.value || uploading.value) return false
  try {
    return sessionStorage.getItem(managedImportStorageKey(identity.value.id, baseId.value)) !== null
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
const importColumns = [
  { title: '文件名称', key: 'name' },
  { title: '大小', key: 'size', width: 100 }
]
function searchImportFiles(): void {
  managedImport.page = 1
  void loadImportFiles()
}
async function loadImportFiles(): Promise<void> {
  if (managedImport.saving || managedImport.uncertain) return
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
    if (ticket.isCurrent() && !isAborted(reason)) managedImport.error = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) managedImport.loading = false
  }
}
function openManagedImport(): void {
  const userId = identity.value?.id,
    id = baseId.value
  if (
    !userId ||
    !selected.value?.can_manage ||
    actionBusy.value ||
    uploading.value ||
    (busy.value && !importRecoverable.value)
  )
    return
  pendingImport = null
  Object.assign(managedImport, {
    open: true,
    loading: false,
    saving: false,
    uncertain: false,
    recoveryBlocked: false,
    error: '',
    search: '',
    files: [],
    ids: [],
    names: [],
    size: selected.value.chunk_size,
    overlap: selected.value.chunk_overlap,
    key: '',
    page: 1,
    total: 0,
    folder: folderId.value
  })
  try {
    const raw = sessionStorage.getItem(managedImportStorageKey(userId, id))
    if (raw !== null) {
      const draft = parseManagedImportDraft(JSON.parse(raw), userId, id)
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
  managedImport.key = crypto.randomUUID()
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
  if (managedImport.saving || managedImport.uncertain || managedImport.recoveryBlocked) return
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
  const userId = identity.value?.id,
    id = baseId.value
  if (
    managedImport.saving ||
    managedImport.recoveryBlocked ||
    !selected.value?.can_manage ||
    !userId
  )
    return
  if (!managedImport.ids.length || !validChunks(managedImport.size, managedImport.overlap)) {
    managedImport.error = '请选择文件并填写有效的切片大小与重叠大小'
    return
  }
  const draft: ManagedImportDraft = pendingImport ?? {
    version: 1,
    userId,
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
  if (draft.userId !== userId || draft.baseId !== id) {
    managedImport.error = '当前用户或知识库已变化，请重新打开导入。'
    return
  }
  try {
    sessionStorage.setItem(managedImportStorageKey(userId, id), JSON.stringify(draft))
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
    clearManagedImportDraft(draft)
    if (ticket.isCurrent()) {
      managedImport.open = false
      managedImport.uncertain = false
      await acceptJob(job)
    }
  } catch (reason) {
    if (reason instanceof ApiError && reason.status < 500) {
      clearManagedImportDraft(draft)
      if (ticket.isCurrent()) {
        managedImport.uncertain = false
        managedImport.key = crypto.randomUUID()
      }
    }
    if (ticket.isCurrent() && !isAborted(reason)) managedImport.error = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) managedImport.saving = false
  }
}

watch(baseId, () => {
  importScope.replace()
  pendingImport = null
  managedImport.open = false
  managedImport.saving = false
  downloadScope.replace()
  downloading.value = false
  previewFile.value = null
})
onBeforeUnmount(() => {
  importScope.dispose()
  downloadScope.dispose()
})

const uploadLabels = {
  queued: '等待上传',
  uploading: '上传中',
  uploaded: '已上传',
  failed: '上传失败'
}
</script>
<template>
  <main class="app-container ai-knowledge-center">
    <a-alert
      v-if="capabilities && !capabilities.milvus_available"
      type="warning"
      message="向量服务当前不可用，请检查服务配置或稍后重试。"
      show-icon
    />
    <a-alert v-if="actionError && !deletion.open" type="error" :message="actionError" show-icon />
    <KnowledgeOverview
      v-if="!baseId"
      :databases="bases"
      :graph-available="capabilities?.graph_available === true"
      :can-manage-storage="hasAnyPermission(identity, 'ai:storage:read', 'ai:storage:write')"
      :loading="loading"
      :error="listError"
      @refresh="loadList"
      @create="openEditor('create')"
      @storage="router.push({ name: 'files' })"
      @edit="openEditor('base', $event)"
      @acl="acl?.open($event)"
      @remove="askDelete('base', $event)"
      @open="navigate"
    />
    <section v-else-if="!selected" class="knowledge-route-state">
      <WorkspaceToolbar title="知识库"
        ><template #actions
          ><a-button @click="back"><ArrowLeftOutlined />返回知识库</a-button></template
        ></WorkspaceToolbar
      >
      <WorkspaceState
        :kind="detailLoading ? 'loading' : 'error'"
        :message="detailError"
        :title="detailLoading ? '正在加载知识库' : '知识库不存在或无权访问'"
        @retry="refresh"
      />
    </section>
    <template v-else>
      <KnowledgeDocuments
        v-if="view === 'documents'"
        :database="selected"
        :graph-available="capabilities?.graph_available === true"
        :folders="folders"
        :documents="documents"
        :folder-id="folderId"
        :loading="detailLoading"
        :error="detailError"
        :observation-error="observationError"
        :busy="busy"
        :import-recoverable="importRecoverable"
        @back="back"
        @refresh="refresh"
        @navigate="navigate(selected, $event)"
        @select-folder="folderId = $event"
        @create-folder="openEditor('createFolder')"
        @upload="openUpload"
        @import="openManagedImport"
        @download="downloadOriginal({ id: $event.file_id, name: $event.file_name })"
        @preview="showPreview"
        @rename-folder="openEditor('folder', $event)"
        @rechunk="openEditor('rechunk', $event)"
        @remove-folder="askDelete('folder', $event)"
        @remove-document="askDelete('document', $event)"
        @source-acl="showSourceAcl"
      >
        <template #jobs>
          <div v-if="selected.can_manage" class="knowledge-job-controls">
            <a-button :disabled="busy" @click="askDelete('rebuild', selected)"
              >重建全部索引</a-button
            ><a-button @click="acl?.open(selected)">知识库权限</a-button>
          </div>
          <KnowledgeJobs
            :jobs="jobs"
            :documents="documents"
            :busy="actionBusy"
            @cancel="cancelJob"
          />
        </template>
      </KnowledgeDocuments>
      <KnowledgeGraphPage
        v-else-if="view === 'graph'"
        :database="selected"
        :labels="graphWorkspace.labels.value"
        :graph="chart"
        :loading="graphWorkspace.loading.value"
        :error="graphWorkspace.error.value"
        :job="graphWorkspace.build.value"
        :jobs="graphWorkspace.jobs.value"
        :available="capabilities?.graph_available === true && graphState?.available === true"
        :build-loading="graphWorkspace.loading.value"
        :starting="graphWorkspace.starting.value"
        :can-edit="selected.can_manage && graphState?.can_manage === true"
        :chat-options="chatOptions"
        :config-state="graphState"
        :config-ready="!!graphState"
        :config-saving="graphWorkspace.saving.value"
        :documents="documents"
        :preview="graphWorkspace.preview.value"
        :readable="!!graphState?.active_revision && !graphState.requires_rebuild"
        :uncertain="graphWorkspace.uncertain.value || graphWorkspace.configUncertain.value"
        :recoverable="graphWorkspace.recoverable.value"
        :stopping="graphWorkspace.stopping.value"
        @back="back"
        @navigate="navigate(selected, $event)"
        @load="graphWorkspace.loadGraph"
        @refresh="graphWorkspace.load"
        @save-config="graphWorkspace.save"
        @start="graphWorkspace.start($event.config, 'build', { rebuild: $event.rebuild })"
        @preview="
          graphWorkspace.start($event.config, 'preview', { document_id: $event.document_id })
        "
        @cancel="graphWorkspace.cancel"
        @recover="graphWorkspace.recover"
      />
      <KnowledgeQuery
        v-else
        :database="selected"
        :result="graphTarget ? null : queryResult"
        :graph-result="graphTarget ? graphQuery.result.value : null"
        :restored-graph="graphTarget"
        :restored-mode="graphQuery.selectedMode.value"
        :graph-available="capabilities?.graph_available === true"
        :loading="queryLoading"
        :error="queryFailure"
        :elapsed="elapsed"
        :recoverable="queryRecovery"
        @back="back"
        @refresh="graphTarget ? graphQuery.clear() : clearQuery()"
        @navigate="navigate(selected, $event)"
        @query="submitSelectedQuery"
        @cancel="graphTarget ? graphQuery.stop() : stopQuery()"
        @recover="graphTarget ? graphQuery.recover() : recoverQuery()"
        @source="showPreview"
        @graph-source="
          showPreview({
            ...$event,
            title: '图谱来源',
            knowledge_base_id: baseId,
            index_revision: selected.active_revision ?? '',
            source_id: $event.document_id,
            score: 0
          })
        "
      />
    </template>
    <a-modal
      :open="editor.open"
      cancel-text="取消"
      :title="editorTitle"
      centered
      width="min(600px, calc(100vw - 32px))"
      :confirm-loading="actionBusy"
      :closable="!actionBusy && (!uncertain || editor.kind !== 'rechunk')"
      :mask-closable="false"
      :keyboard="!actionBusy && (!uncertain || editor.kind !== 'rechunk')"
      ok-text="保存"
      :ok-button-props="{ disabled: uncertain && editor.kind !== 'rechunk' }"
      :cancel-button-props="{ disabled: actionBusy || (uncertain && editor.kind === 'rechunk') }"
      @cancel="editor.open = false"
      @ok="saveEditor"
    >
      <a-alert
        v-if="editorError"
        class="section-alert"
        type="error"
        :message="editorError"
        show-icon
      />
      <a-alert
        v-if="uncertain"
        class="section-alert"
        type="warning"
        :message="
          editor.kind === 'rechunk'
            ? '提交结果尚未确认。重试会使用同一请求标识；请勿修改本次内容。'
            : '提交结果尚未确认，请关闭后刷新列表核对。'
        "
        show-icon
      />
      <a-form :model="editor" layout="vertical" :disabled="actionBusy || uncertain">
        <a-form-item
          v-if="editor.kind !== 'rechunk'"
          :label="['create', 'base'].includes(editor.kind) ? '知识库名称' : '文件夹名称'"
          required
          ><a-input
            v-model:value="editor.name"
            :maxlength="100"
            :aria-label="['create', 'base'].includes(editor.kind) ? '知识库名称' : '文件夹名称'"
        /></a-form-item>
        <a-form-item v-if="['create', 'base'].includes(editor.kind)" label="描述"
          ><a-textarea
            v-model:value="editor.description"
            :maxlength="2000"
            :rows="3"
            aria-label="知识库描述"
        /></a-form-item>
        <template v-if="editor.kind === 'create'">
          <a-form-item label="向量模型" name="embedModel" required
            ><a-select
              :value="editor.embedModel || undefined"
              :options="embeddingOptions"
              aria-label="向量模型"
              placeholder="选择向量模型"
              @update:value="editor.embedModel = typeof $event === 'string' ? $event : ''"
          /></a-form-item>
          <a-alert
            class="section-alert"
            type="info"
            :message="
              capabilities?.graph_available
                ? '新知识库及上传原文默认私有。图谱可在图谱页面配置；OCR 解析尚未接通。'
                : '新知识库及上传原文默认私有。图谱和 OCR 解析尚未接通。'
            "
            show-icon
          />
        </template>
        <ChunkFields
          v-if="['create', 'rechunk'].includes(editor.kind)"
          v-model:size="editor.size"
          v-model:overlap="editor.overlap"
        />
      </a-form>
    </a-modal>
    <a-modal
      :open="deletion.open"
      cancel-text="取消"
      :title="deletion.kind === 'rebuild' ? '重建全部索引' : '确认删除'"
      centered
      :confirm-loading="actionBusy"
      :closable="!actionBusy && !uncertain"
      :mask-closable="false"
      :keyboard="!actionBusy && !uncertain"
      :ok-text="deletion.kind === 'rebuild' ? '重建' : '删除'"
      :ok-button-props="{ danger: deletion.kind !== 'rebuild' }"
      :cancel-button-props="{ disabled: actionBusy || uncertain }"
      @cancel="deletion.open = false"
      @ok="confirmDelete"
    >
      <a-alert
        v-if="actionError"
        class="section-alert"
        type="error"
        :message="actionError"
        show-icon
      />
      <p>{{ deletion.kind === 'rebuild' ? '确定重建' : '确定删除' }}「{{ deletionName }}」？</p>
      <p class="form-hint">
        {{
          deletion.kind === 'rebuild'
            ? '将使用当前向量模型重新处理全部文档，产生模型调用用量。构建失败时保留旧索引。'
            : deletion.kind === 'folder'
              ? '文件夹及其子目录中的文档会一并移出知识库。受管原文按文件保留规则处理。'
              : '文档会从知识索引中移除；原文件的其他业务引用保持有效。'
        }}
      </p>
      <a-alert
        v-if="uncertain"
        type="warning"
        message="提交结果尚未确认；重试将沿用同一请求标识。"
        show-icon
      />
    </a-modal>
    <a-modal
      :open="uploadOpen"
      cancel-text="取消"
      title="上传文档"
      centered
      width="min(640px, calc(100vw - 32px))"
      :closable="!uploading && !uncertain"
      :mask-closable="false"
      :keyboard="!uploading && !uncertain"
      :cancel-button-props="{ disabled: uploading || uncertain }"
      :confirm-loading="uploading"
      :ok-button-props="{ disabled: uploadDiscarding }"
      :ok-text="uncertain ? '确认原提交' : '上传并建立索引'"
      @cancel="closeUpload"
      @ok="submitUpload"
    >
      <p class="form-hint">
        {{ selected?.name }} · 支持 PDF、DOCX、XLSX、TXT、Markdown、CSV、JSON 和 HTML；扫描件需
        OCR，MinerU 继续暂缓。每批最多 20 个文件，单文件最大 20MiB。
      </p>
      <a-alert
        v-if="uploadNotice"
        class="section-alert"
        type="info"
        :message="uploadNotice"
        show-icon
      />
      <a-alert
        v-if="uploadError"
        class="section-alert"
        type="error"
        :message="uploadError"
        show-icon
      />
      <a-form layout="vertical" :disabled="uploading || uncertain || uploadDiscarding"
        ><ChunkFields v-model:size="uploadChunks.size" v-model:overlap="uploadChunks.overlap"
      /></a-form>
      <a-upload-dragger
        multiple
        :before-upload="queueFile"
        :show-upload-list="false"
        :disabled="uploading || uncertain || uploadDiscarding"
        accept=".pdf,.docx,.xlsx,.txt,.md,.csv,.json,.html,.htm"
        ><p class="ant-upload-drag-icon"><InboxOutlined /></p>
        <p>点击选择或拖拽文档到此处</p></a-upload-dragger
      >
      <div class="upload-results" aria-live="polite">
        <div v-for="item in uploadItems" :key="item.id" class="upload-result">
          <div>
            <span class="upload-name">{{ item.name }}</span
            ><a-tag
              :color="
                item.status === 'failed'
                  ? 'error'
                  : item.status === 'uploaded'
                    ? 'success'
                    : 'processing'
              "
              >{{ uploadLabels[item.status] }}</a-tag
            >
          </div>
          <p v-if="item.error" class="inline-error">{{ item.error }}</p>
        </div>
      </div>
      <p class="form-hint">
        原文权限与知识库权限分别生效，新原文默认仅所有者可读。建立索引将产生向量模型用量。
      </p>
    </a-modal>
    <a-modal
      :open="managedImport.open"
      cancel-text="取消"
      title="导入受管文件"
      centered
      width="min(720px, calc(100vw - 32px))"
      :mask-closable="false"
      :closable="!managedImport.saving && !managedImport.uncertain"
      :keyboard="!managedImport.saving && !managedImport.uncertain"
      :confirm-loading="managedImport.saving"
      :ok-button-props="{ disabled: managedImport.recoveryBlocked }"
      :ok-text="managedImport.uncertain ? '确认原提交' : '导入并建立索引'"
      :cancel-button-props="{ disabled: managedImport.saving || managedImport.uncertain }"
      @cancel="managedImport.open = false"
      @ok="submitManagedImport"
    >
      <p class="form-hint">
        选择有权读取的原始文件，每批最多 20
        个。文件原文权限继续独立生效，建立索引会产生向量模型用量。
      </p>
      <a-alert
        v-if="managedImport.error"
        class="section-alert"
        type="error"
        :message="managedImport.error"
        show-icon
      />
      <a-alert
        v-if="managedImport.uncertain"
        class="section-alert"
        type="warning"
        message="提交结果尚未确认，请确认原提交；本次文件和请求标识已保留。"
        show-icon
      />
      <ul v-if="managedImport.uncertain" class="import-recovery-files">
        <li v-for="file in managedImport.names" :key="file.id">{{ file.name }}</li>
      </ul>
      <a-form
        layout="vertical"
        :disabled="managedImport.saving || managedImport.uncertain || managedImport.recoveryBlocked"
        ><ChunkFields v-model:size="managedImport.size" v-model:overlap="managedImport.overlap"
      /></a-form>
      <a-input
        v-model:value="managedImport.search"
        class="import-search"
        aria-label="搜索受管文件"
        placeholder="搜索文件名称，按回车查询"
        :disabled="managedImport.saving || managedImport.uncertain || managedImport.recoveryBlocked"
        @press-enter="searchImportFiles"
      />
      <ResourceTable
        v-if="!managedImport.uncertain && !managedImport.recoveryBlocked"
        :columns="importColumns"
        :rows="managedImport.files"
        :loading="managedImport.loading"
        row-key="fileId"
        :scroll-x="400"
        empty-text="没有可导入的文件"
      >
        <template #bodyCell="{ column, record }"
          ><a-checkbox
            v-if="column.key === 'name'"
            :checked="managedImport.ids.includes(record.fileId)"
            :disabled="managedImport.saving || managedImport.uncertain || !canImport(record)"
            :aria-label="'选择受管文件：' + record.displayName"
            @change="toggleImport(record.fileId, $event.target.checked ?? false)"
            >{{ record.displayName
            }}<span v-if="!canImport(record)" class="form-hint"
              >（已导入或不可索引）</span
            ></a-checkbox
          ><span v-else>{{ formatSize(record.fileSize) }}</span></template
        >
      </ResourceTable>
      <div class="import-pagination">
        <span>已选 {{ managedImport.ids.length }} 个</span
        ><a-pagination
          v-model:current="managedImport.page"
          :total="managedImport.total"
          :page-size="20"
          size="small"
          :show-size-changer="false"
          :disabled="managedImport.saving || managedImport.uncertain"
          @change="loadImportFiles"
        />
      </div>
    </a-modal>
    <a-modal
      :open="preview.open"
      :title="preview.name"
      centered
      width="min(900px, calc(100vw - 32px))"
      @cancel="preview.open = false"
      ><a-spin :spinning="preview.loading"
        ><a-alert v-if="preview.error" type="error" :message="preview.error" show-icon />
        <pre v-else class="document-preview">{{
          preview.text || (preview.loading ? '正在读取文档…' : '文档暂无可预览内容')
        }}</pre></a-spin
      ><template #footer
        ><a-button @click="preview.open = false">关闭</a-button
        ><a-button
          :disabled="preview.loading || !!preview.error"
          :loading="downloading"
          @click="downloadOriginal()"
          >下载原文</a-button
        ></template
      ></a-modal
    >
    <FileAclDrawer
      :file="sourceAclFile"
      :users="[]"
      :user-lookup="findUsers"
      @close="sourceAclFile = null"
      @refresh="refresh"
    />
    <KnowledgeAclDrawer ref="acl" @refresh="refresh" />
    <span v-if="preparation" class="sr-only" aria-live="polite">{{ preparation.status }}</span>
  </main>
</template>
<style scoped lang="scss">
.ai-knowledge-center,
.knowledge-route-state {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
}
.section-alert {
  margin-bottom: 16px;
}
.form-hint {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}
.document-preview {
  max-height: min(65dvh, 700px);
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
  color: var(--color-text);
}
.upload-results {
  margin-top: 14px;
  max-height: 35dvh;
  overflow: auto;
}
.upload-result {
  padding: 10px 0;
  border-bottom: 1px solid var(--gray-150);
}
.upload-result > div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}
.upload-name {
  min-width: 0;
  overflow-wrap: anywhere;
}
.inline-error {
  color: var(--color-error);
}
.knowledge-job-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 18px;
}
.import-recovery-files {
  margin: 12px 0;
  padding-left: 22px;
  overflow-wrap: anywhere;
}
.import-search {
  margin-bottom: 14px;
}
.import-pagination {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
</style>
