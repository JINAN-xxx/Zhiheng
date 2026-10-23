<!-- Adapted from original FileRetentionReminderDrawer, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  DatePicker as ADatePicker,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Pagination as APagination,
  Select as ASelect,
  Space as ASpace,
  Tag as ATag,
  Textarea as ATextarea,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import {
  CheckOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SearchOutlined
} from '@ant-design/icons-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  disposeExpiredFile,
  extendFile,
  getFile,
  getFileReferences,
  getFileReminders,
  markFileReminderRead,
  runFileJob
} from '../api/index.ts'
import type { FileReminder, FileUser, ManagedFile } from '../api/index.ts'
import { formatTime, isoDateTime } from '../presentation.ts'
const props = defineProps<{ users: FileUser[] }>()
const emit = defineEmits<{ refresh: [] }>()
const visible = ref(false),
  loading = ref(false),
  busy = ref(false),
  error = ref(''),
  extendOpen = ref(false),
  formError = ref('')
const reminders = ref<FileReminder[]>([]),
  ids = ref<string[]>([]),
  current = ref<ManagedFile | null>(null)
const query = reactive({
  name: '',
  kind: undefined as string | undefined,
  status: undefined as string | undefined,
  page: 1,
  size: 10
})
const form = reactive({ expiresAt: '', reason: '' })
const scope = new RequestScope(),
  writes = new RequestScope(),
  { confirm } = useConfirmation()
const filtered = computed(() =>
  reminders.value.filter(
    (row) =>
      row.originalName.includes(query.name) &&
      (!query.kind || row.kind === query.kind) &&
      (!query.status || (query.status === 'read') === row.isRead)
  )
)
const rows = computed(() =>
  filtered.value.slice((query.page - 1) * query.size, query.page * query.size)
)
const selectable = computed(() => rows.value.filter((row) => !row.isRead))
const allSelected = computed(
  () => selectable.value.length > 0 && selectable.value.every((row) => ids.value.includes(row.id))
)
const columns: TableColumnsType<FileReminder> = [
  { title: '', key: 'selection', width: 48 },
  { title: '文件名称', dataIndex: 'originalName', key: 'name', width: 180 },
  { title: '所有者', key: 'owner', width: 120 },
  { title: '提醒类型', key: 'kind', width: 110 },
  { title: '到期时间', key: 'expiresAt', width: 180 },
  { title: '状态', key: 'status', width: 90 },
  { title: '业务引用', dataIndex: 'referenceCount', key: 'refs', width: 90 },
  { title: '提醒时间', key: 'createdAt', width: 180 },
  { title: '读取人', key: 'readBy', width: 110 },
  { title: '操作', key: 'actions', width: 100 }
]
function label(id: string | null): string {
  return id ? (props.users.find((user) => user.id === id)?.username ?? id) : '—'
}
function close(): void {
  scope.replace()
  writes.replace()
  visible.value = false
  extendOpen.value = false
  reminders.value = []
  current.value = null
  busy.value = false
  loading.value = false
  ids.value = []
}
function open(): void {
  close()
  visible.value = true
  error.value = ''
  query.page = 1
  void load()
}
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  try {
    const values = await getFileReminders(context.signal)
    if (context.isCurrent() && visible.value) {
      reminders.value = values
      ids.value = []
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function reset(): void {
  Object.assign(query, { name: '', kind: undefined, status: undefined, page: 1 })
}
function select(row: FileReminder, checked: boolean): void {
  if (row.isRead || busy.value) return
  ids.value = checked
    ? [...new Set([...ids.value, row.id])]
    : ids.value.filter((id) => id !== row.id)
}
async function command(action: (signal: AbortSignal) => Promise<void>): Promise<void> {
  if (busy.value) return
  const context = writes.replace()
  busy.value = true
  error.value = ''
  try {
    await action(context.signal)
    if (context.isCurrent() && visible.value) {
      emit('refresh')
      await load()
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) busy.value = false
  }
}
async function scan(): Promise<void> {
  await command(async (signal) => {
    const job = await runFileJob('reminders', signal)
    if (job.status === 'failed') throw new Error(job.errors.join('；') || '扫描失败')
  })
}
async function read(): Promise<void> {
  const selected = [...ids.value]
  await command(async (signal) => {
    for (const id of selected) await markFileReminderRead(id, signal)
  })
}
async function extend(row: FileReminder): Promise<void> {
  await command(async (signal) => {
    const file = await getFile(row.fileId, signal)
    signal.throwIfAborted()
    current.value = file
    Object.assign(form, { expiresAt: '', reason: '' })
    formError.value = ''
    extendOpen.value = true
  })
}
async function submitExtend(): Promise<void> {
  if (!current.value || busy.value) return
  const reason = form.reason.trim()
  let expiresAt: string
  try {
    expiresAt = isoDateTime(form.expiresAt) ?? ''
    if (!expiresAt || !reason || [...reason].length > 500 || Date.parse(expiresAt) <= Date.now())
      throw new Error()
  } catch {
    formError.value = '请填写未来的到期时间和不超过500字的延期原因'
    return
  }
  const target = { ...current.value }
  await command(async (signal) => {
    await extendFile(target, expiresAt, reason, signal)
    signal.throwIfAborted()
    extendOpen.value = false
    current.value = null
  })
}
async function dispose(row: FileReminder): Promise<void> {
  await command(async (signal) => {
    const [file, refs] = await Promise.all([
      getFile(row.fileId, signal),
      getFileReferences(row.fileId, signal)
    ])
    signal.throwIfAborted()
    if (refs.some((ref) => !ref.retainUntil || Date.parse(ref.retainUntil) > Date.now()))
      throw new Error('存在永久或尚未到期的业务引用，暂不可处置')
    if (!(await confirm(`将“${file.displayName}”移入回收站并解除已到期引用？`))) return
    signal.throwIfAborted()
    await disposeExpiredFile(file, signal)
  })
}
onBeforeUnmount(() => {
  scope.dispose()
  writes.dispose()
})
defineExpose({ open })
</script>
<template>
  <ResourceDrawer :open="visible" title="文件保留提醒" width="88%" @close="close">
    <a-alert v-if="error" :message="error" type="error" show-icon class="mb8" />
    <a-alert
      message="显示最近100条提醒，可在下表筛选。到期处置前会重新检查业务引用。"
      type="info"
      show-icon
      class="mb8"
    />
    <a-form :model="query" layout="inline" class="query-form">
      <a-form-item label="文件名称"
        ><a-input
          v-model:value="query.name"
          allow-clear
          placeholder="请输入文件名称"
          @press-enter="query.page = 1"
      /></a-form-item>
      <a-form-item label="提醒类型"
        ><a-select
          :value="query.kind"
          allow-clear
          placeholder="请选择提醒类型"
          :options="[
            { label: '即将到期', value: 'expiring' },
            { label: '已到期', value: 'expired' }
          ]"
          @update:value="(value) => (query.kind = typeof value === 'string' ? value : undefined)"
      /></a-form-item>
      <a-form-item label="提醒状态"
        ><a-select
          :value="query.status"
          allow-clear
          placeholder="请选择提醒状态"
          :options="[
            { label: '未读', value: 'unread' },
            { label: '已读', value: 'read' }
          ]"
          @update:value="(value) => (query.status = typeof value === 'string' ? value : undefined)"
      /></a-form-item>
      <a-form-item
        ><a-space
          ><a-button type="primary" @click="query.page = 1"><SearchOutlined />搜索</a-button
          ><a-button @click="reset"><ReloadOutlined />重置</a-button></a-space
        ></a-form-item
      >
    </a-form>
    <a-space class="mb8"
      ><a-button type="primary" :loading="busy" @click="scan"><ReloadOutlined />扫描</a-button
      ><a-button :disabled="busy || !ids.length" @click="read"
        ><CheckOutlined />已读</a-button
      ></a-space
    >
    <ResourceTable :columns="columns" :rows="rows" :loading="loading" row-key="id" :scroll-x="1200">
      <template #headerCell="{ column }"
        ><a-checkbox
          v-if="column.key === 'selection'"
          :checked="allSelected"
          :disabled="busy"
          aria-label="全选未读提醒"
          @change="(event) => (ids = event.target.checked ? selectable.map((row) => row.id) : [])"
        /><template v-else>{{
          columns.find((item) => item.key === column.key)?.title
        }}</template></template
      >
      <template #bodyCell="{ column, record: row }">
        <a-checkbox
          v-if="column.key === 'selection'"
          :checked="ids.includes(row.id)"
          :disabled="row.isRead || busy"
          @change="(event) => select(row, event.target.checked ?? false)"
        />
        <template v-else-if="column.key === 'owner'">{{ label(row.ownerId) }}</template>
        <a-tag
          v-else-if="column.key === 'kind'"
          :color="row.kind === 'expired' ? 'red' : 'orange'"
          >{{ row.kind === 'expired' ? '已到期' : '即将到期' }}</a-tag
        >
        <template v-else-if="column.key === 'expiresAt'">{{ formatTime(row.expireTime) }}</template>
        <a-tag v-else-if="column.key === 'status'" :color="row.isRead ? 'green' : 'orange'">{{
          row.isRead ? '已读' : '未读'
        }}</a-tag>
        <template v-else-if="column.key === 'createdAt'">{{ formatTime(row.createdAt) }}</template
        ><template v-else-if="column.key === 'readBy'">{{ label(row.readBy) }}</template>
        <a-space v-else-if="column.key === 'actions'"
          ><a-tooltip title="延长保留期限"
            ><a-button type="link" :disabled="busy" aria-label="延长保留期限" @click="extend(row)"
              ><ClockCircleOutlined /></a-button></a-tooltip
          ><a-tooltip v-if="row.kind === 'expired'" title="到期处置"
            ><a-button
              type="link"
              danger
              :disabled="busy"
              aria-label="到期处置"
              @click="dispose(row)"
              ><DeleteOutlined /></a-button></a-tooltip
        ></a-space>
      </template>
    </ResourceTable>
    <a-pagination
      v-if="filtered.length"
      v-model:current="query.page"
      v-model:page-size="query.size"
      :total="filtered.length"
      show-size-changer
    />
  </ResourceDrawer>
  <a-modal :open="extendOpen" title="延长文件保留期限" :width="520" @cancel="extendOpen = false">
    <a-alert v-if="formError" :message="formError" type="error" />
    <a-form :model="form" layout="vertical" :disabled="busy"
      ><a-form-item label="文件名称">{{ current?.displayName }}</a-form-item
      ><a-form-item label="原到期时间">{{ formatTime(current?.expireTime) }}</a-form-item
      ><a-form-item label="新到期时间"
        ><a-date-picker
          v-bind="form.expiresAt ? { value: form.expiresAt } : {}"
          value-format="YYYY-MM-DD HH:mm:ss"
          show-time
          style="width: 100%"
          @update:value="
            (value) => (form.expiresAt = typeof value === 'string' ? value : '')
          " /></a-form-item
      ><a-form-item label="延期原因"
        ><a-textarea
          v-model:value="form.reason"
          :rows="3"
          :maxlength="1000"
          show-count /></a-form-item
    ></a-form>
    <template #footer
      ><a-button @click="extendOpen = false">取消</a-button
      ><a-button type="primary" :loading="busy" @click="submitExtend">确定</a-button></template
    >
  </a-modal>
</template>
<style scoped>
.query-form {
  margin-bottom: 16px;
  row-gap: 8px;
}
:deep(.ant-pagination) {
  margin-top: 16px;
}
</style>
