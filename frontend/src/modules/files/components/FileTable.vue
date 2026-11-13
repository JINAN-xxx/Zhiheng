<!-- Adapted from original FileTable, MIT licensed. -->
<template>
  <ResourceTable
    :loading-delay="150"
    :columns="columns"
    :rows="fileList"
    :loading="loading"
    row-key="fileId"
    :scroll-x="900"
  >
    <template #headerCell="{ column }">
      <a-checkbox
        v-if="column.key === 'selection'"
        aria-label="全选文件"
        :checked="allSelected"
        :indeterminate="someSelected"
        @change="toggleAll"
      />
      <template v-else>{{ columns.find((item) => item.key === column.key)?.title }}</template>
    </template>
    <template #bodyCell="{ column, record: row }">
      <a-checkbox
        v-if="column.key === 'selection'"
        :aria-label="`选择文件：${row.displayName}`"
        :disabled="immutable(row)"
        :checked="selectedIds.includes(row.fileId)"
        @change="(event) => toggleRow(row, event.target?.checked ?? false)"
      />
      <template v-else-if="column.key === 'originalName'">
        <span class="file-name"
          ><FileTextOutlined /><span>{{ row.displayName }}</span></span
        >
        <small v-if="row.displayName !== row.originalName" class="file-summary">{{
          row.originalName
        }}</small>
        <small class="file-summary"
          >{{ formatFileSize(row.fileSize) }} ·
          {{ fileTypeLabel(row.originalName, row.mimeType) }}</small
        >
      </template>
      <template v-else-if="column.key === 'source'">
        <span>{{ userLabel(row.uploaderUserId) }}</span>
        <small class="file-source">{{ row.sourceFileId ? '派生文件' : '上传文件' }}</small>
      </template>
      <template v-else-if="column.key === 'createTime'">{{ formatTime(row.createTime) }}</template>
      <a-tag
        v-else-if="column.key === 'status'"
        :color="row.status === 'active' ? 'green' : row.status === 'deleted' ? 'default' : 'orange'"
        >{{ statusLabel(row.status) }}</a-tag
      >
      <a-space v-else-if="column.key === 'actions'" :size="2" class="file-actions">
        <a-tooltip title="详情"
          ><a-button
            type="link"
            :aria-label="`查看文件：${row.displayName}`"
            :disabled="locked(row)"
            @click="emit('view', row)"
            ><EyeOutlined /></a-button
        ></a-tooltip>
        <a-tooltip v-if="row.status === 'active'" title="下载"
          ><a-button
            type="link"
            :aria-label="`下载文件：${row.displayName}`"
            :disabled="locked(row)"
            @click="emit('download', row)"
            ><DownloadOutlined /></a-button
        ></a-tooltip>
        <template v-if="canManageFile(identity, row)">
          <a-tooltip
            v-if="row.status === 'active' && hasAnyPermission(identity, 'ai:storage:delete')"
            :title="row.referenceCount ? '存在业务引用，无法移入回收站' : '移入回收站'"
          >
            <a-button
              type="link"
              danger
              :aria-label="`删除文件：${row.displayName}`"
              :disabled="immutable(row) || row.referenceCount > 0"
              @click="emit('delete', row)"
              ><DeleteOutlined
            /></a-button>
          </a-tooltip>
          <a-tooltip
            v-if="row.status === 'deleted' && hasAnyPermission(identity, 'ai:storage:maintain')"
            title="恢复"
            ><a-button
              type="link"
              :aria-label="`恢复文件：${row.displayName}`"
              :disabled="immutable(row)"
              @click="emit('restore', row)"
              ><RollbackOutlined /></a-button
          ></a-tooltip>
          <a-tooltip
            v-if="
              ['deleted', 'purging'].includes(row.status) &&
              hasAnyPermission(identity, 'ai:storage:delete')
            "
            :title="
              row.referenceCount
                ? '存在业务引用，无法永久清理'
                : row.status === 'purging'
                  ? '重试永久清理'
                  : '永久清理'
            "
          >
            <a-button
              type="link"
              danger
              :aria-label="`永久清理：${row.displayName}`"
              :disabled="immutable(row) || row.referenceCount > 0"
              @click="emit('purge', row)"
              ><DeleteFilled
            /></a-button>
          </a-tooltip>
          <a-dropdown :trigger="['click']">
            <a-button
              type="link"
              :aria-label="`更多操作：${row.displayName}`"
              :disabled="locked(row)"
              ><MoreOutlined
            /></a-button>
            <template #overlay>
              <a-menu>
                <a-menu-item
                  v-if="row.status === 'active' && hasAnyPermission(identity, 'ai:storage:acl')"
                  key="acl"
                  :aria-label="`访问权限：${row.displayName}`"
                  :disabled="immutable(row)"
                  @click="emit('acl', row)"
                  ><LockOutlined />访问权限</a-menu-item
                >
                <a-menu-item
                  v-if="
                    row.status === 'active' && hasAnyPermission(identity, 'ai:storage:transfer')
                  "
                  key="transfer"
                  :aria-label="`转移文件：${row.displayName}`"
                  :disabled="immutable(row)"
                  @click="emit('transfer', row)"
                  ><SwapOutlined />转移文件</a-menu-item
                >
                <a-menu-item
                  key="reference"
                  :aria-label="`业务引用：${row.displayName}`"
                  @click="emit('reference', row)"
                  ><LinkOutlined />业务引用</a-menu-item
                >
                <a-menu-item
                  key="audit"
                  :aria-label="`文件审计：${row.displayName}`"
                  @click="emit('audit', row)"
                  ><AuditOutlined />文件审计</a-menu-item
                >
              </a-menu>
            </template>
          </a-dropdown>
        </template>
      </a-space>
    </template>
  </ResourceTable>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TableColumnsType } from 'ant-design-vue'
import {
  Button as AButton,
  Checkbox as ACheckbox,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Space as ASpace,
  Tag as ATag,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  AuditOutlined,
  DeleteFilled,
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  LinkOutlined,
  LockOutlined,
  MoreOutlined,
  RollbackOutlined,
  SwapOutlined
} from '@ant-design/icons-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { identity, hasAnyPermission } from '../../identity/public.ts'
import { canManageFile } from '../access.ts'
import { fileTypeLabel, formatFileSize, formatTime, statusLabel } from '../presentation.ts'
import type { FileUser, ManagedFile } from '../api/index.ts'
const props = defineProps<{
  fileList: ManagedFile[]
  loading: boolean
  busyIds: string[]
  selectedIds: string[]
  users: FileUser[]
}>()
const emit = defineEmits<{
  'selection-change': [rows: ManagedFile[]]
  view: [row: ManagedFile]
  download: [row: ManagedFile]
  reference: [row: ManagedFile]
  acl: [row: ManagedFile]
  transfer: [row: ManagedFile]
  audit: [row: ManagedFile]
  delete: [row: ManagedFile]
  restore: [row: ManagedFile]
  purge: [row: ManagedFile]
}>()
const selectableRows = computed(() => props.fileList.filter((row) => !immutable(row)))
const allSelected = computed(
  () =>
    selectableRows.value.length > 0 &&
    selectableRows.value.every((row) => props.selectedIds.includes(row.fileId))
)
const someSelected = computed(() => props.selectedIds.length > 0 && !allSelected.value)
const columns: TableColumnsType<ManagedFile> = [
  { title: '', key: 'selection', width: 48 },
  { title: '文件名称', key: 'originalName', width: 300, ellipsis: true },
  { title: '状态', key: 'status', width: 90 },
  { title: '来源', key: 'source', width: 140, ellipsis: true },
  { title: '上传时间', key: 'createTime', width: 180 },
  { title: '操作', key: 'actions', width: 160, fixed: 'right' }
]
function locked(row: ManagedFile): boolean {
  return props.busyIds.includes(row.fileId)
}
function immutable(row: ManagedFile): boolean {
  return (
    !canManageFile(identity.value, row) ||
    locked(row) ||
    row.sourceFileId !== null ||
    row.status === 'purged'
  )
}
function userLabel(id: string): string {
  return props.users.find((user) => user.id === id)?.username ?? id
}
function toggleRow(row: ManagedFile, checked: boolean): void {
  if (immutable(row)) return
  const ids = checked
    ? [...new Set([...props.selectedIds, row.fileId])]
    : props.selectedIds.filter((id) => id !== row.fileId)
  emit(
    'selection-change',
    props.fileList.filter((file) => ids.includes(file.fileId))
  )
}
function toggleAll(event: { target: { checked?: boolean } }): void {
  emit('selection-change', event.target.checked ? selectableRows.value : [])
}
</script>

<style scoped>
.file-name {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
}
.file-name > :deep(.anticon) {
  color: var(--main-700);
}
.file-name > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-summary,
.file-source {
  display: block;
  margin-top: 3px;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-summary {
  padding-left: 22px;
}
.file-actions :deep(.ant-btn) {
  width: 30px;
  padding-inline: 6px;
}
</style>
