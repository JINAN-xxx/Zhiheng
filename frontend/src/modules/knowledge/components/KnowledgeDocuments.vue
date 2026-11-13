<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  FileTextOutlined,
  FileWordOutlined,
  FilePdfOutlined,
  FileExcelOutlined,
  FolderOutlined,
  FolderAddOutlined,
  MoreOutlined,
  SearchOutlined
} from '@ant-design/icons-vue'
import {
  Alert as AAlert,
  Breadcrumb as ABreadcrumb,
  BreadcrumbItem as ABreadcrumbItem,
  Button as AButton,
  Dropdown as ADropdown,
  Input as AInput,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { identity, canUseCapability } from '../../identity/public.ts'
import type { KnowledgeBase, KnowledgeDocument, KnowledgeFolder } from '../api/index.ts'
import { folderTrail as getTrail, formatSize, formatTime, documentStatus } from '../presentation.ts'
const props = defineProps<{
  database: KnowledgeBase
  graphAvailable?: boolean
  folders: KnowledgeFolder[]
  documents: KnowledgeDocument[]
  folderId: string | null
  loading: boolean
  error: string
  observationError?: string
  busy: boolean
}>()
const emit = defineEmits<{
  back: []
  refresh: []
  navigate: [view: string]
  'select-folder': [id: string | null]
  'create-folder': []
  upload: []
  download: [doc: KnowledgeDocument]
  preview: [doc: KnowledgeDocument]
  'rename-folder': [folder: KnowledgeFolder]
  rechunk: [doc: KnowledgeDocument]
  retry: [doc: KnowledgeDocument]
  'remove-folder': [folder: KnowledgeFolder]
  'remove-document': [doc: KnowledgeDocument]
  rebuild: []
  acl: []
  settings: []
  remove: []
  migration: []
}>()
const search = ref('')
const owner = computed(
  () => props.database.can_manage && props.database.owner_id === identity.value?.id
)
const folderTrail = computed(() => getTrail(props.folders, props.folderId))
const visibleFolders = computed(() =>
  props.folders.filter(
    (f) =>
      f.parent_id === props.folderId &&
      f.name.toLowerCase().includes(search.value.trim().toLowerCase())
  )
)
const visibleDocuments = computed(() =>
  props.documents.filter(
    (d) =>
      d.folder_id === props.folderId &&
      d.file_name.toLowerCase().includes(search.value.trim().toLowerCase())
  )
)
const rows = computed(() => [
  ...visibleFolders.value.map((f) => ({ ...f, isFolder: true as const, rowKey: 'folder-' + f.id })),
  ...visibleDocuments.value.map((d) => ({
    ...d,
    isFolder: false as const,
    rowKey: 'doc-' + d.id
  }))
])
const columns = [
  { title: '资料名称', key: 'name' },
  { title: '状态', key: 'status', width: 112 },
  { title: '添加时间', key: 'time', width: 170 },
  { title: '操作', key: 'actions', width: 134 }
]
function extension(name: string): string {
  return name.split('.').at(-1)?.toLowerCase() ?? ''
}
function fileIcon(name: string) {
  const kind = extension(name)
  return kind === 'docx'
    ? FileWordOutlined
    : kind === 'pdf'
      ? FilePdfOutlined
      : kind === 'xlsx' || kind === 'csv'
        ? FileExcelOutlined
        : FileTextOutlined
}
function createFolder(): void {
  search.value = ''
  emit('create-folder')
}
watch([() => props.database.id, () => props.folderId], () => {
  search.value = ''
})
</script>
<template>
  <section class="knowledge-documents">
    <div class="knowledge-documents__content">
      <a-alert
        v-if="database.document_policy !== 'library'"
        type="warning"
        show-icon
        message="此知识库尚未迁移，继续使用原有资料权限。所有者需预览并确认访问变化后迁移。"
        class="document-alert"
        ><template v-if="owner && canUseCapability('knowledge', 'update')" #action
          ><a-button :disabled="busy" @click="emit('migration')">预览并迁移</a-button></template
        ></a-alert
      >
      <a-alert
        v-if="database.index_status === 'reindex_required'"
        type="warning"
        show-icon
        message="向量服务配置已变化，请先修复索引再添加资料。"
        class="document-alert"
      >
        <template v-if="owner && canUseCapability('knowledge', 'update')" #action
          ><a-button :disabled="busy" @click="emit('settings')">修复索引</a-button></template
        >
      </a-alert>
      <div class="document-controls">
        <a-input v-model:value="search" allow-clear aria-label="搜索资料" placeholder="搜索当前目录"
          ><template #prefix><SearchOutlined /></template
        ></a-input>
        <div class="document-controls__actions">
          <span>{{ visibleFolders.length }} 个文件夹 · {{ visibleDocuments.length }} 份资料</span>
          <a-button
            v-if="owner && canUseCapability('knowledge', 'create')"
            :disabled="busy"
            aria-label="新建文件夹"
            @click="createFolder"
            ><FolderAddOutlined />新建文件夹</a-button
          >
        </div>
      </div>
      <a-breadcrumb class="document-path"
        ><a-breadcrumb-item
          ><a-button type="link" @click="emit('select-folder', null)"
            >根目录</a-button
          ></a-breadcrumb-item
        ><a-breadcrumb-item v-for="folder in folderTrail" :key="folder.id"
          ><a-button type="link" @click="emit('select-folder', folder.id)">{{
            folder.name
          }}</a-button></a-breadcrumb-item
        ></a-breadcrumb
      >
      <a-alert
        v-if="observationError"
        type="warning"
        :message="observationError + '；正在自动重试，后台任务仍会继续。'"
        show-icon
        class="document-alert"
      />
      <a-alert v-if="error" type="warning" :message="error" show-icon class="document-alert" />
      <slot name="jobs" />
      <ResourceTable
        :key="folderId ?? 'root'"
        class="document-table"
        :columns="columns"
        :rows="rows"
        row-key="rowKey"
        :scroll-x="720"
        :pagination="{ pageSize: 15, hideOnSinglePage: true, showSizeChanger: false }"
        :empty-text="
          search.trim()
            ? '没有找到匹配的文件夹或资料'
            : folderId
              ? '此文件夹还没有资料'
              : '还没有资料，添加后系统会自动处理'
        "
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <div class="document-identity">
              <span
                class="document-icon"
                :class="record.isFolder ? 'is-folder' : `is-${extension(record.file_name)}`"
                aria-hidden="true"
              >
                <FolderOutlined v-if="record.isFolder" /><component
                  :is="fileIcon(record.file_name)"
                  v-else
                />
              </span>
              <div class="document-copy">
                <button
                  type="button"
                  class="document-name"
                  :title="record.isFolder ? record.name : record.file_name"
                  :aria-label="record.isFolder ? record.name : record.file_name"
                  @click="
                    record.isFolder ? emit('select-folder', record.id) : emit('preview', record)
                  "
                >
                  {{ record.isFolder ? record.name : record.file_name }}
                </button>
                <p v-if="!record.isFolder" class="document-meta">
                  {{ extension(record.file_name).toUpperCase() }}<span>·</span
                  >{{ formatSize(record.file_size) }}
                  <template v-if="record.parsing_engine"
                    ><span>·</span
                    >{{ record.parsing_engine === 'native' ? '基础解析' : 'MinerU' }}</template
                  >
                </p>
              </div>
            </div>
          </template>
          <template v-else-if="column.key === 'status'"
            ><span v-if="record.isFolder">文件夹</span
            ><template v-else
              ><span class="document-status" :class="`is-${record.status}`"
                ><span class="document-status-dot" aria-hidden="true" />{{
                  documentStatus(record.status)
                }}{{
                  record.status === 'failed' && record.active_revision ? '（旧索引可用）' : ''
                }}</span
              >
              <p v-if="record.error" class="document-error">{{ record.error.message }}</p>
              <a-button
                v-if="
                  record.status === 'failed' && owner && canUseCapability('knowledge', 'import')
                "
                type="link"
                size="small"
                :disabled="busy"
                @click="emit('retry', record)"
                >重试处理</a-button
              ></template
            ></template
          >
          <template v-else-if="column.key === 'time'"
            ><time class="document-time" :datetime="record.created_at">{{
              formatTime(record.created_at)
            }}</time></template
          >
          <template v-else-if="column.key === 'actions'"
            ><a-button
              v-if="!record.isFolder"
              type="link"
              size="small"
              :aria-label="'查看：' + record.file_name"
              @click="emit('preview', record)"
              >查看</a-button
            ><a-button
              v-if="!record.isFolder"
              type="link"
              size="small"
              :aria-label="'下载：' + record.file_name"
              @click="emit('download', record)"
              >下载</a-button
            >
            <a-dropdown
              v-if="
                owner &&
                (canUseCapability('knowledge', 'update') || canUseCapability('knowledge', 'delete'))
              "
              :trigger="['click']"
              ><a-button
                type="text"
                size="small"
                :aria-label="'更多：' + (record.isFolder ? record.name : record.file_name)"
                ><MoreOutlined /></a-button
              ><template #overlay
                ><a-menu
                  ><a-menu-item
                    v-if="record.isFolder"
                    key="rename"
                    :disabled="busy"
                    @click="emit('rename-folder', record)"
                    >重命名</a-menu-item
                  ><a-menu-item
                    v-else
                    key="rechunk"
                    :disabled="busy"
                    @click="emit('rechunk', record)"
                    >高级：处理参数</a-menu-item
                  ><a-menu-item
                    v-if="canUseCapability('knowledge', 'delete')"
                    key="remove"
                    danger
                    :disabled="busy"
                    @click="
                      record.isFolder
                        ? emit('remove-folder', record)
                        : emit('remove-document', record)
                    "
                    >移除</a-menu-item
                  ></a-menu
                ></template
              ></a-dropdown
            ></template
          >
        </template>
      </ResourceTable>
    </div>
  </section>
</template>
<style scoped>
.knowledge-documents {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg-container);
}
.knowledge-documents__content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 18px 0 28px;
}
.document-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.document-controls :deep(.ant-input-affix-wrapper) {
  width: min(320px, 100%);
  height: 34px;
  border-radius: 7px;
}
.document-controls__actions {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
}
.document-controls__actions > span {
  white-space: nowrap;
}
.document-alert {
  margin-bottom: 16px;
}
.document-path {
  margin-bottom: 16px;
}
.document-table {
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  overflow: hidden;
}
.document-table :deep(.ant-table) {
  border-radius: 0;
  font-size: 13px;
}
.document-table :deep(.ant-table-thead > tr > th) {
  padding: 11px 16px;
  background: var(--gray-25);
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
  border-bottom-color: var(--gray-150);
}
.document-table :deep(.ant-table-thead > tr > th::before) {
  display: none;
}
.document-table :deep(.ant-table-tbody > tr > td) {
  padding: 15px 16px;
  border-bottom-color: var(--gray-100);
}
.document-table :deep(.ant-table-tbody > tr:last-child > td) {
  border-bottom: 0;
}
.document-table :deep(.ant-table-pagination) {
  margin: 12px 16px;
}
.document-table :deep(.ant-btn-link) {
  padding: 0 6px;
  font-size: 12px;
  color: var(--color-text-secondary);
}
.document-table :deep(.ant-btn-link:hover) {
  color: var(--main-color);
}
.document-identity {
  display: flex;
  min-width: 0;
  gap: 12px;
  align-items: center;
}
.document-icon {
  display: grid;
  place-items: center;
  flex: 0 0 34px;
  width: 34px;
  height: 40px;
  border-radius: 7px;
  background: var(--gray-50);
  color: var(--main-600);
  font-size: 20px;
}
.document-icon.is-pdf {
  color: var(--workspace-danger);
}
.document-icon.is-xlsx,
.document-icon.is-csv {
  color: var(--workspace-success);
}
.document-icon.is-folder {
  background: var(--main-30);
}
.document-copy {
  min-width: 0;
  flex: 1;
}
.document-name {
  display: -webkit-box;
  max-width: 100%;
  padding: 0;
  margin: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.55;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.document-name:hover {
  color: var(--main-color);
}
.document-name:focus-visible {
  outline: 2px solid var(--main-300);
  outline-offset: 3px;
  border-radius: 2px;
}
.document-meta {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}
.document-meta span {
  opacity: 0.6;
}
.document-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}
.document-status-dot {
  flex: 0 0 5px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--color-text-tertiary);
}
.document-status.is-ready .document-status-dot {
  background: var(--workspace-success);
}
.document-status.is-processing .document-status-dot,
.document-status.is-pending .document-status-dot {
  background: var(--main-color);
}
.document-status.is-failed .document-status-dot {
  background: var(--workspace-danger);
}
.document-time {
  color: var(--color-text-secondary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.document-error {
  color: var(--color-error);
  font-size: 12px;
  max-width: 300px;
  margin: 6px 0;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .document-controls {
    align-items: stretch;
    flex-direction: column;
    gap: 12px;
  }
  .document-controls__actions {
    justify-content: space-between;
  }
  .knowledge-documents__content {
    padding: 16px 0;
  }
}
</style>
