<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowLeftOutlined, InboxOutlined } from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Select as ASelect,
  Spin as ASpin,
  Tag as ATag,
  Textarea as ATextarea,
  UploadDragger as AUploadDragger
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { FileAclDrawer } from '../../files/public.ts'
import KnowledgeOverview from '../components/KnowledgeOverview.vue'
import KnowledgeDocuments from '../components/KnowledgeDocuments.vue'
import KnowledgeQuery from '../components/KnowledgeQuery.vue'
import KnowledgeGraphPage from '../components/KnowledgeGraphPage.vue'
import { chartGraph } from '../graphPresentation.ts'
import KnowledgeAclDrawer from '../components/KnowledgeAclDrawer.vue'
import ChunkFields from '../components/ChunkFields.vue'
import { useKnowledgeWorkspace } from '../composables/workspace.ts'
import { jobStatus } from '../presentation.ts'
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
        @retry="loadDetail"
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
        :busy="busy"
        @back="back"
        @refresh="refresh"
        @navigate="navigate(selected, $event)"
        @select-folder="folderId = $event"
        @create-folder="openEditor('createFolder')"
        @upload="openUpload"
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
          <section v-if="jobs.length" class="knowledge-jobs" aria-label="索引任务">
            <div v-for="job in jobs" :key="job.id" class="knowledge-job">
              <div>
                <a-tag
                  :color="
                    job.status === 'failed'
                      ? 'error'
                      : job.status === 'completed'
                        ? 'success'
                        : 'processing'
                  "
                  >{{ jobStatus(job.status) }}</a-tag
                ><span>{{ job.completed_documents }}/{{ job.total_documents }} 个文档</span
                ><span v-if="job.failed_documents"> · {{ job.failed_documents }} 个失败</span
                ><span v-if="job.chunk_count !== null"> · {{ job.chunk_count }} 个切片</span>
              </div>
              <a-button
                v-if="job.cancel_allowed"
                size="small"
                :disabled="actionBusy"
                @click="cancelJob(job)"
                >取消任务</a-button
              >
              <p v-if="job.error">{{ job.error.message }}</p>
              <p
                v-for="result in job.document_results.filter((item) => item.error)"
                :key="result.document_id"
              >
                {{ result.error?.message }}
              </p>
            </div>
          </section>
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
        OCR。
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
      :open="preview.open"
      :title="preview.name"
      centered
      width="min(900px, calc(100vw - 32px))"
      :footer="null"
      @cancel="preview.open = false"
      ><a-spin :spinning="preview.loading"
        ><a-alert v-if="preview.error" type="error" :message="preview.error" show-icon />
        <pre v-else class="document-preview">{{
          preview.text || (preview.loading ? '正在读取文档…' : '文档暂无可预览内容')
        }}</pre>
      </a-spin></a-modal
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
  margin-bottom: 12px;
}
.knowledge-jobs {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
  max-height: 240px;
  overflow: auto;
}
.knowledge-job {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
}
.knowledge-job p {
  width: 100%;
  margin: 0;
  color: var(--color-error);
}
</style>
