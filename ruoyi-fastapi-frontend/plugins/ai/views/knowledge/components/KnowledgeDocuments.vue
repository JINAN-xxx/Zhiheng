<template>
  <section class="knowledge-documents">
    <WorkspaceToolbar :title="database.name" :subtitle="'文档管理 · ' + typeLabel(database.kb_type)">
      <template #actions>
        <a-button @click="emit('back')">
          <template #icon><ArrowLeftOutlined /></template>
          返回知识库
        </a-button>
        <a-tooltip title="刷新文档">
          <a-button shape="circle" :loading="loading" aria-label="刷新文档" @click="emit('refresh')">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button v-if="can('edit')" :disabled="busy" @click="emit('create-folder')">
          <template #icon><FolderAddOutlined /></template>
          新建文件夹
        </a-button>
        <a-button v-if="can('edit')" type="primary" :disabled="busy" @click="emit('upload')">
          <template #icon><UploadOutlined /></template>
          上传文档
        </a-button>
      </template>
    </WorkspaceToolbar>

    <div class="knowledge-documents__content">
      <KnowledgeSectionNav
        active-view="documents"
        :graph-enabled="database.enable_knowledge_graph"
        @navigate="emit('navigate', $event)"
      />

      <div class="document-controls">
        <a-input v-model:value="search" allow-clear placeholder="搜索当前目录文件" aria-label="搜索文档">
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-select v-model:value="kind" aria-label="筛选文件类型" :options="kindOptions" />
        <a-select v-model:value="time" aria-label="筛选上传时间" :options="timeOptions" />
        <span class="document-total">{{ visibleFolderCount }} 个文件夹 · {{ visibleDocumentCount }} 个文档</span>
      </div>

      <div class="document-path">
        <a-breadcrumb>
          <a-breadcrumb-item>
            <a-button type="link" :disabled="busy" @click="emit('select-folder', null)">全部文档</a-button>
          </a-breadcrumb-item>
          <a-breadcrumb-item v-for="folder in folderTrail" :key="folder.id">
            <a-button type="link" :disabled="busy" @click="emit('select-folder', folder.id)">{{ folder.name }}</a-button>
          </a-breadcrumb-item>
        </a-breadcrumb>
      </div>

      <WorkspaceState v-if="loading && !rows.length" kind="loading" />
      <WorkspaceState v-else-if="error && !rows.length" kind="error" :message="error" @retry="emit('refresh')" />
      <template v-else>
        <a-alert v-if="error" class="document-alert" type="warning" :message="error" show-icon />
        <a-table
        :columns="columns"
        :loading="loading"
        :data-source="rows"
        row-key="rowKey"
        size="middle"
        :scroll="{ x: 760 }"
        :pagination="{ pageSize: 15, hideOnSinglePage: true, showSizeChanger: false }"
        :locale="{ emptyText: '当前目录暂无文档' }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <a-button
              type="link"
              class="document-name"
              :disabled="busy"
              @click="record.isFolder ? emit('select-folder', record.id) : emit('preview', record)"
            >
              <FolderOutlined v-if="record.isFolder" />
              <FileTextOutlined v-else />
              {{ record.file_name || record.name }}
            </a-button>
          </template>
          <template v-else-if="column.key === 'status'">
            <span v-if="record.isFolder">文件夹</span>
            <a-tag
              v-else
              :color="['failed', 'error'].includes(record.status) ? 'error' : ['processing', 'pending'].includes(record.status) ? 'processing' : undefined"
            >
              {{ documentStatus(record.status) }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'size'">{{ record.isFolder ? '—' : formatSize(record.file_size) }}</template>
          <template v-else-if="column.key === 'chunks'">{{ record.isFolder ? '—' : (record.chunk_size || '—') + ' / ' + (record.chunk_overlap ?? '—') }}</template>
          <template v-else-if="column.key === 'time'">{{ formatTime(record.created_at) }}</template>
          <template v-else-if="column.key === 'actions'">
            <a-space :size="0">
              <a-button
                v-if="can('edit')"
                type="link"
                size="small"
                :disabled="busy"
                @click="emit('edit', { kind: record.isFolder ? 'renameFolder' : 'rechunk', item: record })"
              >
                {{ record.isFolder ? '重命名' : '重新切片' }}
              </a-button>
              <a-button
                v-if="can('remove')"
                type="link"
                size="small"
                danger
                :disabled="busy"
                @click="emit('remove', { kind: record.isFolder ? 'folder' : 'document', item: record })"
              >
                删除
              </a-button>
            </a-space>
          </template>
        </template>
        </a-table>
      </template>
    </div>
  </section>
</template>

<script setup>
import {
  ArrowLeftOutlined,
  FileTextOutlined,
  FolderAddOutlined,
  FolderOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'
import { computed, ref, watch } from 'vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { filterKnowledgeDocuments, knowledgeFolderTrail } from '../../../utils/knowledge.js'
import KnowledgeSectionNav from './KnowledgeSectionNav.vue'

const props = defineProps({
  database: { type: Object, required: true },
  folders: { type: Array, default: () => [] },
  documents: { type: Array, default: () => [] },
  folderId: { type: [String, Number], default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  permissions: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'back',
  'refresh',
  'navigate',
  'select-folder',
  'create-folder',
  'upload',
  'preview',
  'edit',
  'remove',
])

const search = ref('')
const kind = ref('all')
const time = ref('')
const kindOptions = [
  { value: 'all', label: '全部类型' },
  { value: 'folders', label: '仅文件夹' },
  { value: 'files', label: '仅文件' },
]
const timeOptions = [
  { value: '', label: '全部时间' },
  { value: 'today', label: '今天' },
  { value: 'week', label: '最近 7 天' },
  { value: 'month', label: '最近 30 天' },
]
const columns = [
  { title: '文件名', key: 'name', width: 250 },
  { title: '状态', key: 'status', width: 95 },
  { title: '大小', key: 'size', width: 90 },
  { title: '切片 / 重叠', key: 'chunks', width: 115 },
  { title: '上传时间', key: 'time', width: 160 },
  { title: '操作', key: 'actions', width: 155 },
]

const can = action => props.permissions.includes('*:*:*') || props.permissions.includes('ai:knowledge:' + action)
const typeLabel = type => ({ chroma: 'Chroma', milvus: 'Milvus', lightrag: 'LightRAG' })[type] || type || '知识库'
const folderTrail = computed(() => knowledgeFolderTrail(props.folders, props.folderId))
const matchingFolders = computed(() => props.folders.filter(folder => (
  (folder.parent_id || null) === props.folderId
  && (!search.value || folder.name.toLowerCase().includes(search.value.toLowerCase()))
)))
const matchingDocuments = computed(() => filterKnowledgeDocuments(props.documents, {
  folderId: props.folderId,
  search: search.value,
  time: time.value,
}))
const visibleFolderCount = computed(() => kind.value === 'files' ? 0 : matchingFolders.value.length)
const visibleDocumentCount = computed(() => kind.value === 'folders' ? 0 : matchingDocuments.value.length)
const rows = computed(() => [
  ...(kind.value === 'files' ? [] : matchingFolders.value.map(folder => ({ ...folder, rowKey: 'folder-' + folder.id, isFolder: true }))),
  ...(kind.value === 'folders' ? [] : matchingDocuments.value.map(document => ({ ...document, rowKey: 'doc-' + document.id, isFolder: false }))),
])

const documentStatus = status => ({
  done: '已索引',
  completed: '已索引',
  success: '已索引',
  processed: '已索引',
  pending: '等待处理',
  processing: '处理中',
  failed: '失败',
  error: '失败',
})[status] || status || '已索引'

function formatTime(value) {
  if (!value) return '—'
  const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function formatSize(value) {
  const size = Number(value || 0)
  if (size < 1024) return size + ' B'
  if (size < 1048576) return (size / 1024).toFixed(1) + ' KB'
  return (size / 1048576).toFixed(1) + ' MB'
}

watch(() => props.database.db_id, () => {
  search.value = ''
  kind.value = 'all'
  time.value = ''
})
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
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
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
  font-size: 12px;
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
