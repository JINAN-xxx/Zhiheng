<!-- Adapted from original FileAuditDrawer, MIT licensed. -->
<template>
  <ResourceDrawer
    :open="visible"
    :title="`文件审计 - ${fileName}`"
    width="80%"
    @close="closeDrawer"
  >
    <a-form :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="操作类型" name="action"
        ><a-select
          style="min-width: 150px"
          :value="queryParams.action || undefined"
          allow-clear
          placeholder="全部操作"
          :options="actionOptions"
          @update:value="setAction"
      /></a-form-item>
      <a-form-item label="操作结果" name="outcome"
        ><a-select
          style="min-width: 150px"
          :value="queryParams.outcome || undefined"
          allow-clear
          placeholder="全部结果"
          :options="resultOptions"
          @update:value="setOutcome"
      /></a-form-item>
      <a-form-item label="操作用户" name="actorId"
        ><a-select
          style="min-width: 150px"
          :value="queryParams.actorId || undefined"
          show-search
          allow-clear
          option-filter-prop="label"
          placeholder="请选择操作用户"
          :options="userOptions"
          @update:value="setActor"
      /></a-form-item>
      <a-form-item label="操作时间"
        ><a-range-picker
          v-bind="dateRange ? { value: dateRange } : {}"
          value-format="YYYY-MM-DD HH:mm:ss"
          show-time
          :placeholder="['开始时间', '结束时间']"
          @update:value="setDates"
      /></a-form-item>
      <a-form-item
        ><a-space
          ><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button
          ><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space
        ></a-form-item
      >
    </a-form>
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <ResourceTable
      :columns="columns"
      :rows="auditList"
      :loading="loading"
      row-key="id"
      :scroll-x="970"
    >
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'actorName'">{{ actorName(row.actorId) }}</template
        ><template v-else-if="column.key === 'error'">{{ errorCode(row) }}</template
        ><template v-else-if="column.key === 'action'">{{ actionLabel(row.action) }}</template>
        <template v-else-if="column.key === 'result'"
          ><a-tag :color="resultColor(row)">{{ resultLabel(outcome(row)) }}</a-tag></template
        >
        <template v-else-if="column.key === 'bytesSent'">{{
          typeof row.details.bytes_sent === 'number' ? formatFileSize(row.details.bytes_sent) : '-'
        }}</template>
        <template v-else-if="column.key === 'createdAt'">{{ formatTime(row.createdAt) }}</template>
        <template v-else-if="column.key === 'detail'"
          ><a-tooltip title="查看审计详情"
            ><a-button type="link" aria-label="查看审计详情" @click="handleDetail(row)"
              ><EyeOutlined /></a-button></a-tooltip
        ></template>
      </template>
    </ResourceTable>
    <a-pagination
      v-if="total > 0"
      v-model:current="queryParams.pageNum"
      v-model:page-size="queryParams.pageSize"
      :total="total"
      show-size-changer
      @change="getList"
    />
    <template #footer><a-button @click="closeDrawer">关闭</a-button></template>
  </ResourceDrawer>

  <a-modal
    v-if="detail"
    :open="detail !== null"
    title="审计详情"
    :width="720"
    @cancel="detail = null"
  >
    <a-descriptions :column="2" bordered class="audit-descriptions">
      <a-descriptions-item label="操作类型">{{ actionLabel(detail.action) }}</a-descriptions-item
      ><a-descriptions-item label="操作结果">{{
        resultLabel(outcome(detail))
      }}</a-descriptions-item>
      <a-descriptions-item label="操作用户">{{ actorName(detail.actorId) }}</a-descriptions-item
      ><a-descriptions-item label="审计ID">{{ detail.id }}</a-descriptions-item>
      <a-descriptions-item label="操作时间" :span="2">{{
        formatTime(detail.createdAt)
      }}</a-descriptions-item
      ><a-descriptions-item label="失败原因" :span="2">{{ errorCode(detail) }}</a-descriptions-item>
    </a-descriptions>
    <ResourceTable
      v-if="detailEntries.length"
      class="mt20"
      :columns="detailColumns"
      :rows="detailEntries"
      row-key="label"
    />
    <a-empty v-else description="无操作详情" />
    <template #footer><a-button @click="detail = null">关闭</a-button></template>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Descriptions as ADescriptions,
  DescriptionsItem as ADescriptionsItem,
  Empty as AEmpty,
  Form as AForm,
  FormItem as AFormItem,
  Modal as AModal,
  Pagination as APagination,
  RangePicker as ARangePicker,
  Select as ASelect,
  Space as ASpace,
  Tag as ATag,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getFileAuditPage } from '../api/index.ts'
import type { AuditQuery, FileAudit, FileUser, ManagedFile } from '../api/index.ts'
import { actionLabel, formatFileSize, formatTime, isoDateTime } from '../presentation.ts'
const props = defineProps<{ users: FileUser[] }>()
const visible = ref(false),
  loading = ref(false),
  fileName = ref(''),
  fileId = ref(''),
  error = ref('')
const auditList = ref<FileAudit[]>([]),
  total = ref(0),
  detail = ref<FileAudit | null>(null),
  dateRange = ref<[string, string]>()
const userOptions = computed(() =>
  props.users.map((user) => ({ label: user.username, value: user.id }))
)
const initialQuery = (): AuditQuery => ({
  pageNum: 1,
  pageSize: 10,
  action: '',
  outcome: '',
  actorId: '',
  createdFrom: '',
  createdTo: ''
})
const queryParams = reactive(initialQuery()),
  scope = new RequestScope()
const actionOptions = [
  'uploaded',
  'download',
  'updated',
  'acl_updated',
  'transferred',
  'deleted',
  'restored',
  'purged',
  'expiry_extended',
  'expired_references_released',
  'references_released',
  'parsed'
].map((value) => ({ value, label: actionLabel(value) }))
const resultOptions = [
  { value: 'allowed', label: '已授权' },
  { value: 'denied', label: '已拒绝' },
  { value: 'completed', label: '已完成' }
]
const columns: TableColumnsType<FileAudit> = [
  { title: '操作', key: 'action', width: 120 },
  { title: '结果', key: 'result', width: 100 },
  { title: '操作用户', key: 'actorName', width: 160, ellipsis: true },
  { title: '传输字节', key: 'bytesSent', width: 110 },
  { title: '失败原因', key: 'error', width: 160, ellipsis: true },
  { title: '访问时间', key: 'createdAt', width: 180 },
  { title: '详情', key: 'detail', width: 70 }
]
const detailColumns: TableColumnsType<{ label: string; value: string }> = [
  { title: '详情项', dataIndex: 'label', key: 'label', width: 180 },
  { title: '内容', dataIndex: 'value', key: 'value', ellipsis: true }
]
const detailEntries = computed(() =>
  detail.value
    ? Object.entries(detail.value.details).map(([key, value]) => ({
        label: key,
        value: typeof value === 'string' ? value : (JSON.stringify(value, null, 2) ?? '-')
      }))
    : []
)
function actorName(id: string | null): string {
  return id === null ? '系统' : (props.users.find((user) => user.id === id)?.username ?? id)
}
function outcome(row: FileAudit): string {
  return typeof row.details.outcome === 'string' ? row.details.outcome : ''
}
function resultLabel(value: string): string {
  return resultOptions.find((item) => item.value === value)?.label ?? '未记录'
}
function resultColor(row: FileAudit): string {
  return outcome(row) === 'denied' || outcome(row) === 'failed'
    ? 'red'
    : outcome(row) === 'allowed'
      ? 'blue'
      : 'default'
}
function errorCode(row: FileAudit): string {
  return typeof row.details.error_code === 'string' ? row.details.error_code : '-'
}
function setAction(value: unknown): void {
  queryParams.action = typeof value === 'string' ? value : ''
}
function setOutcome(value: unknown): void {
  queryParams.outcome = typeof value === 'string' ? value : ''
}
function setActor(value: unknown): void {
  queryParams.actorId = typeof value === 'string' ? value : ''
}
function setDates(value: unknown): void {
  dateRange.value =
    Array.isArray(value) && typeof value[0] === 'string' && typeof value[1] === 'string'
      ? [value[0], value[1]]
      : undefined
}
function open(file: ManagedFile): void {
  closeDrawer()
  fileId.value = file.sourceFileId ?? file.fileId
  fileName.value = file.displayName
  Object.assign(queryParams, initialQuery())
  dateRange.value = undefined
  visible.value = true
  void getList()
}
async function getList(): Promise<void> {
  const context = scope.replace(),
    id = fileId.value
  loading.value = true
  error.value = ''
  try {
    const result = await getFileAuditPage(
      id,
      {
        ...queryParams,
        createdFrom: isoDateTime(dateRange.value?.[0]) ?? '',
        createdTo: isoDateTime(dateRange.value?.[1]) ?? ''
      },
      context.signal
    )
    if (context.isCurrent() && visible.value && fileId.value === id) {
      auditList.value = result.rows
      total.value = result.total
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function handleQuery(): void {
  queryParams.pageNum = 1
  void getList()
}
function resetQuery(): void {
  Object.assign(queryParams, initialQuery())
  dateRange.value = undefined
  void getList()
}
function handleDetail(row: FileAudit): void {
  detail.value = row
}
function closeDrawer(): void {
  scope.replace()
  visible.value = false
  loading.value = false
  detail.value = null
  auditList.value = []
  total.value = 0
}
onBeforeUnmount(() => scope.dispose())
defineExpose({ open })
</script>

<style scoped>
.query-form {
  margin-bottom: 16px;
  row-gap: 8px;
}
.audit-descriptions :deep(.ant-descriptions-item-label) {
  white-space: nowrap;
}

.audit-descriptions :deep(.ant-descriptions-item-content) {
  overflow-wrap: anywhere;
  word-break: break-word;
}
</style>
