<!-- Adapted from original FileTable, MIT licensed. -->
<template>
  <ResourceTable
    :columns="columns"
    :rows="fileList"
    :loading="loading"
    row-key="fileId"
    :scroll-x="1750"
  >
    <template #headerCell="{ column }"
      ><a-checkbox
        v-if="column.key === 'selection'"
        aria-label="全选文件"
        :checked="allSelected"
        :indeterminate="someSelected"
        @change="toggleAll"
      /><template v-else>{{
        columns.find((item) => item.key === column.key)?.title
      }}</template></template
    >
    <template #bodyCell="{ column, record: row }">
      <a-checkbox
        v-if="column.key === 'selection'"
        :aria-label="`选择文件：${row.displayName}`"
        :disabled="immutable(row)"
        :checked="selectedIds.includes(row.fileId)"
        @change="(event) => toggleRow(row, event.target?.checked ?? false)"
      />
      <a-tag
        v-else-if="column.key === 'accessType'"
        :color="row.accessType === 'public' ? 'green' : 'orange'"
        >{{ row.accessType === 'public' ? '公开' : '受保护' }}</a-tag
      >
      <template v-else-if="column.key === 'originalName'"
        ><span class="file-name"
          ><FileTextOutlined /><span>{{ row.displayName }}</span></span
        ><small v-if="row.displayName !== row.originalName" class="file-original-name">{{
          row.originalName
        }}</small></template
      ><template v-else-if="column.key === 'extension'">{{
        row.originalName.includes('.') ? row.originalName.split('.').pop()?.toLowerCase() : '—'
      }}</template
      ><template v-else-if="column.key === 'createBy'">{{ userLabel(row.uploaderUserId) }}</template
      ><template v-else-if="column.key === 'fileSize'">{{ formatFileSize(row.fileSize) }}</template
      ><template v-else-if="column.key === 'ownerName'">{{ userLabel(row.ownerUserId) }}</template>
      <a-tag v-else-if="column.key === 'expireTime'" :color="expirationTagType(row.expireTime)">{{
        expirationLabel(row.expireTime)
      }}</a-tag>
      <template v-else-if="column.key === 'acl'"
        ><a-tooltip
          v-if="row.aclNearestExpireTime"
          :title="`最近过期：${formatTime(row.aclNearestExpireTime)}`"
          ><a-tag :color="isAclExpiring(row.aclNearestExpireTime) ? 'orange' : 'default'"
            >{{ row.aclEntryCount }} 项<span v-if="isAclExpiring(row.aclNearestExpireTime)">
              · 即将过期</span
            ></a-tag
          ></a-tooltip
        ><span v-else>{{ row.aclEntryCount || 0 }} 项</span></template
      >
      <template v-else-if="column.key === 'reference'"
        ><a-button v-if="row.referenceCount" type="link" @click="emit('reference', row)"
          >{{ row.referenceCount }} 项</a-button
        ><span v-else>0 项</span></template
      >
      <a-tag
        v-else-if="column.key === 'storageStatus'"
        :color="storageStatusTagType(row.storageStatus)"
        >{{ storageStatusLabel(row.storageStatus) }}</a-tag
      >
      <template v-else-if="column.key === 'createTime'">{{ formatTime(row.createTime) }}</template>
      <template v-else-if="column.key === 'status'"
        ><a-tag
          :color="
            row.status === 'active' ? 'green' : row.status === 'deleted' ? 'default' : 'orange'
          "
          >{{ statusLabel(row.status) }}</a-tag
        ></template
      >
      <a-space v-else-if="column.key === 'actions'" :size="2">
        <a-tooltip title="详细"
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
        <a-tooltip v-if="row.status === 'active'" title="授权"
          ><a-button
            type="link"
            :aria-label="`访问权限：${row.displayName}`"
            :disabled="immutable(row)"
            @click="emit('acl', row)"
            ><LockOutlined /></a-button
        ></a-tooltip>
        <a-tooltip v-if="row.status === 'active'" title="转移"
          ><a-button
            type="link"
            :aria-label="`转移文件：${row.displayName}`"
            :disabled="immutable(row)"
            @click="emit('transfer', row)"
            ><SwapOutlined /></a-button
        ></a-tooltip>
        <a-tooltip title="审计"
          ><a-button
            type="link"
            :aria-label="`文件审计：${row.displayName}`"
            :disabled="locked(row)"
            @click="emit('audit', row)"
            ><AuditOutlined /></a-button
        ></a-tooltip>
        <a-tooltip
          v-if="row.status === 'active' && row.referenceCount"
          title="存在业务引用，无法删除"
          ><a-button type="link" danger disabled><DeleteOutlined /></a-button
        ></a-tooltip>
        <a-tooltip v-else-if="row.status === 'active'" title="删除"
          ><a-button
            type="link"
            danger
            :aria-label="`删除文件：${row.displayName}`"
            :disabled="immutable(row)"
            @click="emit('delete', row)"
            ><DeleteOutlined /></a-button
        ></a-tooltip>
        <a-tooltip v-if="row.status === 'deleted'" title="恢复"
          ><a-button
            type="link"
            :aria-label="`恢复文件：${row.displayName}`"
            :disabled="immutable(row)"
            @click="emit('restore', row)"
            ><RollbackOutlined /></a-button
        ></a-tooltip>
        <a-tooltip
          v-if="row.status !== 'active'"
          :title="row.status === 'purging' ? '重试永久清理' : '永久清理'"
          ><a-button
            type="link"
            danger
            :aria-label="`永久清理：${row.displayName}`"
            :disabled="immutable(row)"
            @click="emit('purge', row)"
            ><DeleteFilled /></a-button
        ></a-tooltip>
      </a-space>
    </template>
  </ResourceTable>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TableColumnsType } from 'ant-design-vue'
import {
  Button as AButton,
  Checkbox as ACheckbox,
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
  LockOutlined,
  RollbackOutlined,
  SwapOutlined
} from '@ant-design/icons-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import {
  expirationLabel,
  expirationTagType,
  formatFileSize,
  formatTime,
  isAclExpiring,
  statusLabel,
  storageStatusLabel,
  storageStatusTagType
} from '../presentation.ts'
import type { FileUser, ManagedFile } from '../api/index.ts'
const props = defineProps<{
  fileList: ManagedFile[]
  loading: boolean
  busyIds: string[]
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
const selectedIds = ref<string[]>([])
const selectedRows = computed(() =>
  props.fileList.filter((row) => selectedIds.value.includes(row.fileId))
)
const allSelected = computed(
  () =>
    props.fileList.some((row) => !immutable(row)) &&
    props.fileList
      .filter((row) => !immutable(row))
      .every((row) => selectedIds.value.includes(row.fileId))
)
const someSelected = computed(() => selectedIds.value.length > 0 && !allSelected.value)
const columns: TableColumnsType<ManagedFile> = [
  { title: '', key: 'selection', width: 48 },
  { title: '文件名称', key: 'originalName', width: 220, ellipsis: true },
  { title: '访问类型', key: 'accessType', width: 110 },
  { title: '扩展名', key: 'extension', width: 90 },
  { title: '文件大小', key: 'fileSize', width: 120 },
  { title: '上传用户', key: 'createBy', width: 120, ellipsis: true },
  { title: '所有者', key: 'ownerName', width: 120 },
  { title: '文件有效期', key: 'expireTime', width: 150 },
  { title: 'ACL', key: 'acl', width: 130 },
  { title: '业务引用', key: 'reference', width: 100 },
  { title: '存储状态', key: 'storageStatus', width: 110 },
  { title: '上传时间', key: 'createTime', width: 180 },
  { title: '状态', key: 'status', width: 90 },
  { title: '操作', key: 'actions', width: 300, fixed: 'right' }
]
function locked(row: ManagedFile): boolean {
  return props.busyIds.includes(row.fileId)
}
function immutable(row: ManagedFile): boolean {
  return locked(row) || row.sourceFileId !== null || row.status === 'purged'
}
function userLabel(id: string): string {
  return props.users.find((user) => user.id === id)?.username ?? id
}
function notifySelection(): void {
  emit('selection-change', selectedRows.value)
}
function toggleRow(row: ManagedFile, checked: boolean): void {
  if (immutable(row)) return
  selectedIds.value = checked
    ? [...new Set([...selectedIds.value, row.fileId])]
    : selectedIds.value.filter((id) => id !== row.fileId)
  notifySelection()
}
function toggleAll(event: { target: { checked?: boolean } }): void {
  selectedIds.value = event.target.checked
    ? props.fileList.filter((row) => !immutable(row)).map((row) => row.fileId)
    : []
  notifySelection()
}
watch(
  () => props.fileList,
  () => {
    selectedIds.value = []
    notifySelection()
  }
)
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
.file-original-name {
  display: block;
  margin-top: 3px;
  padding-left: 22px;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-action-disabled-wrapper {
  display: inline-flex;
  align-items: center;
  margin-left: 12px;
  vertical-align: middle;
}
</style>
