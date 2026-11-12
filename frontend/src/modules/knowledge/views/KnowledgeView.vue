<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ArrowLeftOutlined, InboxOutlined } from '@ant-design/icons-vue'
import {
  message as notice,
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Select as ASelect,
  Checkbox as ACheckbox,
  Spin as ASpin,
  Tag as ATag,
  Textarea as ATextarea,
  UploadDragger as AUploadDragger
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { saveBlob } from '../../files/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { KnowledgeDocument, KnowledgeHit } from '../api/index.ts'
import KnowledgeJobs from '../components/KnowledgeJobs.vue'
import KnowledgeOverview from '../components/KnowledgeOverview.vue'
import KnowledgeDocuments from '../components/KnowledgeDocuments.vue'
import KnowledgeQuery from '../components/KnowledgeQuery.vue'
import KnowledgeGraphPage from '../components/KnowledgeGraphPage.vue'
import { chartGraph } from '../graphPresentation.ts'
import KnowledgeAclDrawer from '../components/KnowledgeAclDrawer.vue'
import ChunkFields from '../components/ChunkFields.vue'
import { useKnowledgeWorkspace } from '../composables/workspace.ts'
import { identity } from '../../identity/public.ts'
import { downloadDocument } from '../api/index.ts'
import KnowledgeMigration from '../components/KnowledgeMigration.vue'
import KnowledgeSettings from '../components/KnowledgeSettings.vue'
import KnowledgeWorkspaceHeader from '../components/KnowledgeWorkspaceHeader.vue'
import KnowledgeSectionNav from '../components/KnowledgeSectionNav.vue'
import type { KnowledgeBase, KnowledgeJob } from '../api/index.ts'
import { folderTrail } from '../presentation.ts'
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
  selectFolder,
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
  openUpload,
  selectUploadFolder,
  closeUpload,
  queueFile,
  submitUpload,
  recoverQuery,
  stopQuery,
  retryDocument,
  clearQuery
} = useKnowledgeWorkspace(() => {
  void notice.success('文档处理完成')
})
const queryVisited = ref(view.value === 'query')
watch(view, (value) => {
  if (value === 'query') queryVisited.value = true
})
function folderLocation(id: string | null): string {
  const trail = folderTrail(folders.value, id).map((folder) => folder.name)
  return ['根目录', ...trail].join(' / ')
}
const uploadFolderOptions = computed(() => [
  { value: '', label: '根目录' },
  ...folders.value.map((folder) => ({ value: folder.id, label: folderLocation(folder.id) }))
])
const queryLoading = computed(() => (graphTarget.value ? graphQuery.busy.value : queryBusy.value))
const queryFailure = computed(() => (graphTarget.value ? graphQuery.error.value : queryError.value))
const queryRecovery = computed(() =>
  graphTarget.value ? graphQuery.recoverable.value : queryRecoverable.value
)
const graphState = graphWorkspace.state
const chart = computed(() => chartGraph(graphWorkspace.graph.value))
const chatOptions = computed(() =>
  models.value
    .filter((model) => model.enabled && model.model_type === 'chat')
    .map((model) => ({ value: model.id, label: model.name }))
)
const acl = ref<InstanceType<typeof KnowledgeAclDrawer> | null>(null)
const migration = ref<InstanceType<typeof KnowledgeMigration> | null>(null)
const settings = ref<InstanceType<typeof KnowledgeSettings> | null>(null)
function requestUpload() {
  const base = selected.value
  if (!base) return
  if (base.document_policy !== 'library') {
    void migration.value?.open(base)
    return
  }
  if (base.index_status === 'reindex_required') {
    void settings.value?.open(base)
    return
  }
  void openUpload()
}
async function settingsSaved(_base: KnowledgeBase, job: KnowledgeJob | null) {
  if (job) await acceptJob(job)
  else await refresh()
}
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
const previewFile = ref<{ id: string; name: string; baseId: string } | null>(null)
const downloading = ref(false)
const downloadScope = new RequestScope()
async function showPreview(doc: KnowledgeDocument | KnowledgeHit): Promise<void> {
  previewFile.value = {
    id: 'document_id' in doc ? doc.document_id : doc.id,
    baseId: 'knowledge_base_id' in doc ? doc.knowledge_base_id : doc.base_id,
    name: 'file_name' in doc ? doc.file_name : doc.title
  }
  await workspacePreview(doc)
}
async function downloadOriginal(file = previewFile.value): Promise<void> {
  if (!file || downloading.value) return
  const ticket = downloadScope.replace()
  downloading.value = true
  try {
    const blob = await downloadDocument(file.baseId, file.id, ticket.signal)
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

watch(
  [baseId, () => identity.value?.id, () => selected.value?.id, () => selected.value?.can_manage],
  () => {
    downloadScope.replace()
    downloading.value = false
    previewFile.value = null
  },
  { flush: 'sync' }
)
onBeforeUnmount(() => {
  downloadScope.dispose()
})

const uploadLabels = {
  queued: '等待上传',
  uploading: '上传中',
  uploaded: '已上传',
  failed: '上传失败'
}
const validationMessages = new Set<string>()
let validationScheduled = false
function queueUploadFile(file: File): boolean {
  queueFile(file)
  if (uploadError.value) {
    validationMessages.add(uploadError.value)
    uploadError.value = ''
    if (!validationScheduled) {
      validationScheduled = true
      queueMicrotask(() => {
        void notice.warning({
          content: [...validationMessages].join('；'),
          key: 'knowledge-upload-validation',
          duration: 4
        })
        validationMessages.clear()
        validationScheduled = false
      })
    }
  }
  return false
}
</script>
<template>
  <KnowledgeSettings ref="settings" @saved="settingsSaved" />
  <main
    class="app-container ai-knowledge-center"
    :class="{ 'is-detail': !!baseId && view !== 'graph' }"
  >
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
      :loading="loading"
      :error="listError"
      @refresh="loadList"
      @create="openEditor('create')"
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
      <section v-show="view !== 'graph'" class="knowledge-detail">
        <KnowledgeWorkspaceHeader
          :database="selected"
          :loading="detailLoading"
          :busy="busy"
          :graph-available="capabilities?.graph_available === true"
          @back="back"
          @refresh="refresh"
          @settings="settings?.open(selected)"
          @acl="acl?.open(selected)"
          @upload="requestUpload"
          @rebuild="askDelete('rebuild', selected)"
          @graph="navigate(selected, 'graph')"
          @migration="migration?.open(selected)"
          @remove="askDelete('base', selected)"
        />
        <KnowledgeSectionNav :active-view="view" @navigate="navigate(selected, $event)" />
        <div class="knowledge-panels">
          <div
            v-show="view === 'documents'"
            id="knowledge-documents-panel"
            class="knowledge-panel"
            role="tabpanel"
            aria-label="资料"
          >
            <KnowledgeDocuments
              :database="selected"
              :graph-available="capabilities?.graph_available === true"
              :folders="folders"
              :documents="documents"
              :folder-id="folderId"
              :loading="detailLoading"
              :error="detailError"
              :observation-error="observationError"
              :busy="busy"
              @back="back"
              @refresh="refresh"
              @navigate="navigate(selected, $event)"
              @select-folder="selectFolder"
              @create-folder="openEditor('createFolder')"
              @upload="requestUpload"
              @download="
                downloadOriginal({ id: $event.id, name: $event.file_name, baseId: $event.base_id })
              "
              @preview="showPreview"
              @rename-folder="openEditor('folder', $event)"
              @rechunk="openEditor('rechunk', $event)"
              @remove-folder="askDelete('folder', $event)"
              @remove-document="askDelete('document', $event)"
              @rebuild="askDelete('rebuild', selected)"
              @acl="acl?.open(selected)"
              @settings="settings?.open(selected)"
              @remove="askDelete('base', selected)"
              @migration="migration?.open(selected)"
              @retry="retryDocument"
            >
              <template #jobs>
                <KnowledgeJobs
                  :jobs="jobs"
                  :documents="documents"
                  :busy="actionBusy"
                  @cancel="cancelJob"
                />
              </template>
            </KnowledgeDocuments>
          </div>
          <div
            v-if="queryVisited"
            v-show="view === 'query'"
            id="knowledge-query-panel"
            class="knowledge-panel"
            role="tabpanel"
            aria-label="检索测试"
          >
            <KnowledgeQuery
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
          </div>
        </div>
      </section>
      <KnowledgeGraphPage
        v-if="view === 'graph'"
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
      :ok-button-props="{
        disabled:
          (uncertain && editor.kind !== 'rechunk') ||
          (editor.kind === 'create' &&
            (!capabilities?.default_embedding_configured ||
              !capabilities?.default_rerank_configured))
      }"
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
        <p v-if="editor.kind === 'createFolder'" class="form-hint">
          创建位置：{{ folderLocation(folderId) }}
        </p>
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
        <a-alert
          v-if="editor.kind === 'create'"
          type="info"
          show-icon
          :message="
            capabilities?.default_embedding_configured && capabilities?.default_rerank_configured
              ? '默认仅自己可用。资料自动处理，检索统一经过召回与重排。'
              : '请联系管理员配置有效的默认向量模型和重排模型，配置完成后即可创建知识库。'
          "
        />
        <ChunkFields
          v-if="editor.kind === 'rechunk'"
          v-model:size="editor.size"
          v-model:overlap="editor.overlap"
        />
      </a-form>
    </a-modal>
    <a-modal
      :open="deletion.open"
      cancel-text="取消"
      :title="deletion.kind === 'rebuild' ? '重新处理全部资料' : '确认删除'"
      centered
      :confirm-loading="actionBusy"
      :closable="!actionBusy && !uncertain"
      :mask-closable="false"
      :keyboard="!actionBusy && !uncertain"
      :ok-text="deletion.kind === 'rebuild' ? '重建' : '删除'"
      :ok-button-props="{
        danger: deletion.kind !== 'rebuild',
        disabled:
          deletion.kind === 'base' &&
          (deletion.loadingReferences ||
            deletion.referencesError ||
            !deletion.references ||
            (deletion.references.total > 0 && !deletion.confirmReferences))
      }"
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
      <template v-if="deletion.kind === 'base'">
        <p v-if="deletion.loadingReferences">正在核对关联影响…</p>
        <template v-else-if="deletion.references?.total">
          <p>删除后，以下智能体或应用引用会失效，后续使用将提示知识库不可用。</p>
          <ul>
            <li v-for="(item, index) in deletion.references.items" :key="index">
              <RouterLink v-if="item.path" :to="item.path" @click="deletion.open = false">{{
                item.name
              }}</RouterLink
              ><span v-else>{{ item.name }}</span>
            </li>
          </ul>
          <p v-if="deletion.references.hidden_count">
            另有 {{ deletion.references.hidden_count }} 个无权查看详情的关联。
          </p>
          <a-checkbox
            v-model:checked="deletion.confirmReferences"
            :disabled="actionBusy || uncertain"
            >我已了解关联影响，确认删除</a-checkbox
          >
        </template>
      </template>
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
      title="添加资料"
      centered
      width="min(640px, calc(100vw - 32px))"
      :closable="!uploading && !uncertain"
      :mask-closable="false"
      :keyboard="!uploading && !uncertain"
      :cancel-button-props="{ disabled: uploading || uncertain }"
      :confirm-loading="uploading || uploadDiscovering"
      :ok-button-props="{ disabled: uploadDiscarding || uploadDiscovering }"
      :ok-text="uncertain ? '确认原提交' : '添加并自动处理'"
      @cancel="closeUpload"
      @ok="submitUpload"
    >
      <p class="form-hint">
        {{ selected?.name }} · 支持 PDF、DOCX、XLSX、TXT、Markdown、CSV、JSON 和 HTML；扫描件需
        OCR，当前尚不支持。每批最多 20 个文件，单文件最大 20MiB。
      </p>
      <div class="upload-location">
        <label for="knowledge-upload-folder">存放位置</label>
        <a-select
          id="knowledge-upload-folder"
          :value="uploadFolderId ?? ''"
          :options="uploadFolderOptions"
          :disabled="uploadFolderLocked"
          show-search
          option-filter-prop="label"
          aria-label="存放位置"
          @change="selectUploadFolder($event === '' ? null : String($event))"
        />
        <p class="form-hint">选择文件后，本批次的存放位置将固定。</p>
      </div>
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
      <a-upload-dragger
        multiple
        :before-upload="queueUploadFile"
        :show-upload-list="false"
        :disabled="uploading || uploadDiscovering || uncertain || uploadDiscarding"
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
        添加后自动处理，资料按本知识库的共享范围提供查看和下载。处理会产生模型用量。
      </p>
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
          >下载资料</a-button
        ></template
      ></a-modal
    >
    <KnowledgeAclDrawer ref="acl" @refresh="refresh" />
    <KnowledgeMigration ref="migration" @accepted="acceptJob" @cleanup="acl?.open($event)" />
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
.ai-knowledge-center.is-detail {
  padding: 0;
}
.knowledge-detail {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  width: 100%;
  max-width: 1440px;
  padding: 24px 32px 0;
  margin: 0 auto;
  box-sizing: border-box;
}
.knowledge-panel {
  height: 100%;
  min-width: 0;
  min-height: 0;
}
.knowledge-panels {
  flex: 1;
  min-height: 0;
  min-width: 0;
}
@media (max-width: 760px) {
  .knowledge-detail {
    padding: 18px 18px 0;
  }
}
.section-alert {
  margin-bottom: 16px;
}
.form-hint {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}
.upload-location {
  display: grid;
  gap: 8px;
  margin: 16px 0;
}
.upload-location > label {
  font-size: 14px;
  color: var(--color-text);
}
.upload-location .ant-select {
  min-width: 0;
  width: 100%;
}
.upload-location .form-hint {
  margin: 0;
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
