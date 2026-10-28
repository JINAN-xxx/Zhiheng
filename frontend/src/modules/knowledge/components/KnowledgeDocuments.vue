<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<template>
  <section class="knowledge-documents">
    <WorkspaceToolbar :title="database.name" subtitle="文档管理">
      <template #actions>
        <a-button aria-label="返回知识库" @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="刷新文档">
          <a-button
            shape="circle"
            :loading="loading"
            aria-label="刷新文档"
            @click="emit('refresh')"
          >
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button
          v-if="database.can_manage"
          :disabled="busy"
          aria-label="新建文件夹"
          @click="emit('create-folder')"
        >
          <template #icon><FolderAddOutlined /></template>
          新建文件夹
        </a-button>
        <a-button
          v-if="database.can_manage"
          :disabled="busy && !importRecoverable"
          aria-label="导入受管文件"
          @click="emit('import')"
        >
          <template #icon><ImportOutlined /></template>导入受管文件
        </a-button>
        <a-button
          v-if="database.can_manage"
          type="primary"
          :disabled="busy"
          aria-label="上传文档"
          @click="emit('upload')"
        >
          <template #icon><UploadOutlined /></template>
          上传文档
        </a-button>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-documents__content">
      <KnowledgeSectionNav
        active-view="documents"
        :graph-available="graphAvailable === true"
        @navigate="emit('navigate', $event)"
      />

      <a-alert
        class="document-alert"
        type="info"
        message="原文权限与知识库权限分别生效；共享知识库后，请按需要设置原文的访问权限。"
        show-icon
      />

      <div class="document-controls">
        <a-input
          v-model:value="search"
          allow-clear
          placeholder="搜索当前目录文件"
          aria-label="搜索文档"
        >
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-select
          :value="kind"
          aria-label="筛选文件类型"
          :options="kindOptions"
          @update:value="kind = typeof $event === 'string' ? $event : 'all'"
        />
        <a-select
          :value="time"
          aria-label="筛选上传时间"
          :options="timeOptions"
          @update:value="time = typeof $event === 'string' ? $event : ''"
        />
        <span class="document-total"
          >{{ visibleFolderCount }} 个文件夹 · {{ visibleDocumentCount }} 个文档</span
        >
      </div>

      <div class="document-path">
        <a-breadcrumb>
          <a-breadcrumb-item>
            <a-button type="link" @click="emit('select-folder', null)">全部文档</a-button>
          </a-breadcrumb-item>
          <a-breadcrumb-item v-for="folder in folderTrail" :key="folder.id">
            <a-button type="link" @click="emit('select-folder', folder.id)">{{
              folder.name
            }}</a-button>
          </a-breadcrumb-item>
        </a-breadcrumb>
      </div>

      <WorkspaceState v-if="loading && !rows.length" kind="loading" />
      <WorkspaceState
        v-else-if="error && !rows.length"
        kind="error"
        :message="error"
        @retry="emit('refresh')"
      />
      <template v-else>
        <a-alert v-if="error" class="document-alert" type="warning" :message="error" show-icon />
        <ResourceTable
          :columns="columns"
          :loading="loading"
          :rows="rows"
          row-key="rowKey"
          :scroll-x="760"
          :pagination="{ pageSize: 15, hideOnSinglePage: true, showSizeChanger: false }"
          empty-text="当前目录暂无文档"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <a-button
                type="link"
                class="document-name"
                :aria-label="record.isFolder ? record.name : record.file_name"
                @click="openRow(record)"
              >
                <FolderOutlined v-if="record.isFolder" />
                <FileTextOutlined v-else />
                {{ record.isFolder ? record.name : record.file_name }}
              </a-button>
            </template>
            <template v-else-if="column.key === 'status'">
              <span v-if="record.isFolder">文件夹</span>
              <a-tag
                v-else
                :color="
                  ['failed', 'error'].includes(record.status)
                    ? 'error'
                    : ['processing', 'pending'].includes(record.status)
                      ? 'processing'
                      : 'default'
                "
              >
                {{ documentStatus(record.status)
                }}{{ record.status === 'failed' && record.active_revision ? '（旧索引可用）' : '' }}
              </a-tag>
              <p v-if="!record.isFolder && record.error" class="document-error">
                {{ record.error.message }}
              </p>
            </template>
            <template v-else-if="column.key === 'size'">{{
              record.isFolder ? '—' : formatSize(record.file_size)
            }}</template>
            <template v-else-if="column.key === 'chunks'">{{
              record.isFolder
                ? '—'
                : (record.chunk_size || '—') +
                  ' / ' +
                  (record.chunk_overlap ?? '—') +
                  ' / ' +
                  (record.chunk_count ?? '—')
            }}</template>
            <template v-else-if="column.key === 'time'">{{
              formatTime(record.created_at)
            }}</template>
            <template v-else-if="column.key === 'actions'">
              <a-space :size="0" wrap>
                <a-button
                  v-if="!record.isFolder"
                  type="link"
                  size="small"
                  :aria-label="'下载原文：' + record.file_name"
                  @click="downloadRow(record)"
                  >下载原文</a-button
                >
                <a-button
                  v-if="!record.isFolder && database.can_manage"
                  type="link"
                  size="small"
                  :aria-label="'原文权限：' + record.file_name"
                  @click="sourceAcl(record)"
                  >原文权限</a-button
                >
                <a-button
                  v-if="database.can_manage"
                  type="link"
                  size="small"
                  :disabled="busy"
                  @click="editRow(record)"
                >
                  {{ record.isFolder ? '重命名' : '重新切片' }}
                </a-button>
                <a-button
                  v-if="database.can_manage"
                  type="link"
                  size="small"
                  danger
                  :disabled="busy"
                  @click="removeRow(record)"
                >
                  删除
                </a-button>
              </a-space>
            </template>
          </template>
        </ResourceTable>
      </template>
      <slot name="jobs" />
    </div>
  </section>
</template>

<script setup lang="ts">
import {
  ArrowLeftOutlined,
  FileTextOutlined,
  FolderAddOutlined,
  FolderOutlined,
  ImportOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined
} from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Breadcrumb as ABreadcrumb,
  BreadcrumbItem as ABreadcrumbItem,
  Button as AButton,
  Input as AInput,
  Select as ASelect,
  Space as ASpace,
  Tag as ATag,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { computed, ref, watch } from 'vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import KnowledgeSectionNav from './KnowledgeSectionNav.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import type { KnowledgeBase, KnowledgeDocument, KnowledgeFolder } from '../api/index.ts'
import { folderTrail as getTrail, formatTime, formatSize, documentStatus } from '../presentation.ts'
const props = defineProps<{
  database: KnowledgeBase
  graphAvailable?: boolean
  importRecoverable?: boolean
  folders: KnowledgeFolder[]
  documents: KnowledgeDocument[]
  folderId: string | null
  loading: boolean
  error: string
  busy: boolean
}>()
const emit = defineEmits<{
  back: []
  refresh: []
  navigate: [view: string]
  'select-folder': [id: string | null]
  'create-folder': []
  upload: []
  import: []
  download: [doc: KnowledgeDocument]
  preview: [doc: KnowledgeDocument]
  'rename-folder': [folder: KnowledgeFolder]
  rechunk: [doc: KnowledgeDocument]
  'remove-folder': [folder: KnowledgeFolder]
  'remove-document': [doc: KnowledgeDocument]
  'source-acl': [doc: KnowledgeDocument]
}>()
type Row =
  | (KnowledgeFolder & { isFolder: true; rowKey: string })
  | (KnowledgeDocument & { isFolder: false; rowKey: string })
const search = ref(''),
  kind = ref('all'),
  time = ref('')
const kindOptions = [
  { value: 'all', label: '全部类型' },
  { value: 'folders', label: '仅文件夹' },
  { value: 'files', label: '仅文件' }
]
const timeOptions = [
  { value: '', label: '全部时间' },
  { value: 'today', label: '今天' },
  { value: 'week', label: '最近 7 天' },
  { value: 'month', label: '最近 30 天' }
]
const columns: TableColumnsType<Row> = [
  { title: '文件名', key: 'name', width: 250 },
  { title: '向量状态', key: 'status', width: 160 },
  { title: '大小', key: 'size', width: 90 },
  { title: '切片 / 重叠 / 数量', key: 'chunks', width: 150 },
  { title: '上传时间', key: 'time', width: 160 },
  { title: '操作', key: 'actions', width: 330 }
]
const folderTrail = computed(() => getTrail(props.folders, props.folderId))
const matchingFolders = computed(() =>
  props.folders.filter(
    (folder) =>
      folder.parent_id === props.folderId &&
      folder.name.toLowerCase().includes(search.value.trim().toLowerCase())
  )
)
const matchingDocuments = computed(() =>
  props.documents.filter((doc) => {
    if (
      doc.folder_id !== props.folderId ||
      !doc.file_name.toLowerCase().includes(search.value.trim().toLowerCase())
    )
      return false
    if (!time.value) return true
    const now = new Date(),
      start =
        time.value === 'today'
          ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
          : now.getTime() - (time.value === 'week' ? 7 : 30) * 86400000
    return Date.parse(doc.created_at) >= start
  })
)
const visibleFolderCount = computed(() =>
  kind.value === 'files' ? 0 : matchingFolders.value.length
)
const visibleDocumentCount = computed(() =>
  kind.value === 'folders' ? 0 : matchingDocuments.value.length
)
const rows = computed<Row[]>(() => [
  ...(kind.value === 'files'
    ? []
    : matchingFolders.value.map((folder) => ({
        ...folder,
        rowKey: 'folder-' + folder.id,
        isFolder: true as const
      }))),
  ...(kind.value === 'folders'
    ? []
    : matchingDocuments.value.map((doc) => ({
        ...doc,
        rowKey: 'doc-' + doc.id,
        isFolder: false as const
      })))
])
function openRow(row: Row): void {
  if (row.isFolder) emit('select-folder', row.id)
  else emit('preview', row)
}
function editRow(row: Row): void {
  if (row.isFolder) emit('rename-folder', row)
  else emit('rechunk', row)
}
function removeRow(row: Row): void {
  if (row.isFolder) emit('remove-folder', row)
  else emit('remove-document', row)
}
function downloadRow(row: Row): void {
  if (!row.isFolder) emit('download', row)
}
function sourceAcl(row: Row): void {
  if (!row.isFolder) emit('source-acl', row)
}
watch(
  () => props.database.id,
  () => {
    search.value = ''
    kind.value = 'all'
    time.value = ''
  }
)
</script>
<style scoped lang="scss">
.knowledge-documents {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
}

.knowledge-documents__content {
  min-width: 0;
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 20px 24px 28px;
}

.document-controls {
  display: flex;
  min-height: 54px;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 4px 0 12px;
}

.document-controls :deep(.ant-input-affix-wrapper) {
  width: min(330px, 40%);
}

.document-controls :deep(.ant-select) {
  width: 126px;
}

.document-total {
  margin-left: auto;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.document-error {
  margin: 6px 0 0;
  color: var(--color-error);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.document-alert {
  margin: 14px 0;
}

.document-path {
  display: flex;
  min-height: 42px;
  align-items: center;
  padding: 8px 2px;
  color: var(--color-text-secondary);
}

.document-path :deep(.ant-btn) {
  height: auto;
  padding: 0;
}

.document-name {
  max-width: 250px;
  height: auto;
  padding: 0;
  overflow-wrap: anywhere;
  white-space: normal;
  text-align: left;
}

@media (max-width: 760px) {
  .knowledge-documents__content {
    padding: 16px;
  }

  .document-controls {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .document-controls :deep(.ant-input-affix-wrapper) {
    width: 100%;
  }

  .document-controls :deep(.ant-select) {
    min-width: 0;
    flex: 1 1 120px;
    width: auto;
  }

  .document-total {
    width: 100%;
    margin-left: 0;
    text-align: right;
  }
}
</style>
