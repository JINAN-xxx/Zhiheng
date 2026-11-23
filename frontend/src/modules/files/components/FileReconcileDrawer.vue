<!-- Adapted from original FileReconcileDrawer, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Form as AForm,
  FormItem as AFormItem,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Select as ASelect,
  TabPane as ATabPane,
  Tabs as ATabs,
  Tag as ATag,
  Textarea as ATextarea,
  Tooltip as ATooltip
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { DownOutlined, InfoCircleOutlined } from '@ant-design/icons-vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { waitForBackgroundObservation } from '../../../shared/backgroundObservation.ts'
import {
  getFileJobs,
  getReconcileIssues,
  getReconcileStats,
  resolveReconcileIssue,
  runFileJob
} from '../api/index.ts'
import type { FileJob, ReconcileIssue } from '../api/index.ts'
import { formatFileSize, formatTime } from '../presentation.ts'
const props = defineProps<{ canRead: boolean; canWrite: boolean }>()
const emit = defineEmits<{ refresh: [] }>()
const visible = ref(false),
  loading = ref(false),
  busy = ref(false),
  error = ref(''),
  observationError = ref(''),
  readDenied = ref(false),
  activeTab = ref('issues'),
  modalOpen = ref(false)
const issues = ref<ReconcileIssue[]>([]),
  jobs = ref<FileJob[]>([]),
  current = ref<ReconcileIssue | null>(null),
  status = ref<ReconcileIssue['status']>('open')
const stats = ref<Awaited<ReturnType<typeof getReconcileStats>> | null>(null)
const form = reactive({ action: 'ignore' as Action, reason: '' })
type Action = 'ignore' | 'quarantine' | 'repair'
const actionDetails: Record<Action, { label: string; effect: string }> = {
  ignore: { label: '忽略异常', effect: '该记录会移到“已忽略”，文件的存储状态保持原样。' },
  quarantine: { label: '隔离文件', effect: '隔离后，平台将阻止该文件被下载或使用。' },
  repair: {
    label: '验证恢复',
    effect: '请先在存储端恢复原文件。内容校验通过后，平台会将文件标为正常。'
  }
}
const kinds: Record<ReconcileIssue['kind'], string> = {
  missing: '存储中找不到文件',
  orphan: '文件存在，但系统未登记',
  size_or_hash: '文件大小或内容与记录不符',
  invalidkey: '存储路径无效'
}
const statuses: Record<ReconcileIssue['status'], string> = {
  open: '待处理',
  resolved: '已解决',
  ignored: '已忽略',
  quarantined: '已隔离'
}
const jobStatuses: Record<FileJob['status'], string> = {
  running: '执行中',
  succeeded: '已完成',
  failed: '失败'
}
const scope = new RequestScope(),
  writes = new RequestScope()
const runs = computed(() => jobs.value.filter((job) => job.kind === 'reconcile'))
const running = computed(() => jobs.value.some((job) => job.status === 'running'))
const latestRun = computed(() => runs.value[0])
const statusOptions = computed(() =>
  (Object.keys(statuses) as ReconcileIssue['status'][]).map((value) => ({
    value,
    label: stats.value ? `${statuses[value]} · ${stats.value[value]}` : statuses[value]
  }))
)
const emptyIssuesText = computed(() => {
  if (observationError.value) return '暂时无法读取异常记录'
  if (!stats.value) return '正在读取异常记录'
  return status.value === 'open' ? '暂无待处理的异常记录' : '此状态下没有异常记录'
})
const issueColumns: TableColumnsType<ReconcileIssue> = [
  { title: '发现的问题', key: 'kind', width: 180 },
  { title: '文件编号', key: 'file', width: 175 },
  { title: '文件大小', key: 'size', width: 135 },
  { title: '状态', key: 'status', width: 90 },
  { title: '最近更新', key: 'updatedAt', width: 165 },
  { title: '操作', key: 'actions', width: 88, fixed: 'right' }
]
const visibleIssueColumns = computed(() =>
  issueColumns.filter((column) => column.key !== 'actions' || props.canWrite)
)
const runColumns: TableColumnsType<FileJob> = [
  { title: '检查状态', key: 'status', width: 100 },
  { title: '检查项数', dataIndex: 'scanned', key: 'scanned', width: 100 },
  { title: '检出异常', dataIndex: 'changed', key: 'changed', width: 100 },
  { title: '开始时间', key: 'startedAt', width: 180 },
  { title: '完成时间', key: 'finishedAt', width: 180 },
  { title: '失败原因', key: 'errors', width: 250 }
]
function close(): void {
  scope.replace()
  writes.replace()
  visible.value = false
  modalOpen.value = false
  current.value = null
  issues.value = []
  jobs.value = []
  stats.value = null
  busy.value = false
  loading.value = false
}
function open(): void {
  if (!props.canRead) return
  close()
  visible.value = true
  error.value = ''
  observationError.value = ''
  activeTab.value = 'issues'
  void refresh()
}
function observeNext(context: ContextTicket, delay = 2000): void {
  void waitForBackgroundObservation(context.signal, delay)
    .then(() => {
      if (context.isCurrent() && visible.value) void refresh(true)
    })
    .catch((reason: unknown) => {
      if (context.isCurrent() && !isAborted(reason)) observationError.value = errorMessage(reason)
    })
}
async function refresh(background = false): Promise<void> {
  if (!props.canRead) return
  const context = scope.replace(),
    requested = status.value
  if (!background) loading.value = true
  try {
    const [values, counts, runs] = await Promise.all([
      getReconcileIssues(requested, context.signal),
      getReconcileStats(context.signal),
      getFileJobs(context.signal)
    ])
    if (!context.isCurrent() || !visible.value) return
    issues.value = values
    stats.value = counts
    jobs.value = runs
    readDenied.value = false
    observationError.value = ''
    if (runs.some((job) => job.status === 'running')) observeNext(context)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      observationError.value = errorMessage(reason)
      if (reason instanceof ApiError && [401, 403, 404].includes(reason.status)) {
        issues.value = []
        jobs.value = []
        current.value = null
        modalOpen.value = false
        stats.value = null
        readDenied.value = true
        error.value = ''
      } else if (
        visible.value &&
        running.value &&
        (reason instanceof TypeError ||
          (reason instanceof ApiError &&
            (reason.status >= 500 || [408, 429].includes(reason.status))))
      )
        observeNext(context, 4000)
    }
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function setStatus(value: unknown): void {
  if (['open', 'resolved', 'ignored', 'quarantined'].includes(String(value))) {
    status.value = value as ReconcileIssue['status']
    void refresh()
  }
}
function actions(issue: ReconcileIssue): Action[] {
  if (!props.canWrite || readDenied.value) return []
  if (issue.status === 'quarantined' && issue.fileId && issue.kind !== 'orphan') return ['repair']
  if (issue.status !== 'open') return []
  if (issue.kind === 'orphan') return ['ignore', 'quarantine']
  if (issue.fileId) return ['ignore', 'quarantine', 'repair']
  return ['ignore']
}
function handleCommand(value: unknown, issue: ReconcileIssue): void {
  if (busy.value || running.value || !actions(issue).includes(value as Action)) return
  current.value = { ...issue }
  Object.assign(form, { action: value, reason: '' })
  error.value = ''
  modalOpen.value = true
}
async function command(action: (signal: AbortSignal) => Promise<void>): Promise<void> {
  if (busy.value || running.value || readDenied.value || !props.canWrite) return
  const context = writes.replace()
  busy.value = true
  error.value = ''
  try {
    await action(context.signal)
    if (context.isCurrent() && visible.value) {
      emit('refresh')
      await refresh()
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      const message = errorMessage(reason)
      await refresh()
      if (context.isCurrent() && visible.value && !readDenied.value) error.value = message
    }
  } finally {
    if (context.isCurrent()) busy.value = false
  }
}
async function scan(): Promise<void> {
  await command(async (signal) => {
    const job = await runFileJob('reconcile', signal)
    if (job.status === 'failed') throw new Error(job.errors.join('；') || '检查失败')
  })
}
function cancelAction(): void {
  modalOpen.value = false
  error.value = ''
}
async function submit(): Promise<void> {
  if (!current.value || !props.canWrite) return
  const target = { ...current.value },
    action = form.action,
    reason = form.reason.trim()
  if (!reason || [...reason].length > 500) {
    error.value = '请填写处理原因，最多 500 字'
    return
  }
  await command(async (signal) => {
    await resolveReconcileIssue(target, action, reason, '', signal)
    signal.throwIfAborted()
    modalOpen.value = false
    current.value = null
  })
}
onBeforeUnmount(() => {
  scope.dispose()
  writes.dispose()
})
defineExpose({ open })
</script>
<template>
  <ResourceDrawer :open="visible" title="存储检查" :width="960" @close="close">
    <a-alert
      v-if="(error && !modalOpen) || observationError"
      :message="(!modalOpen && error) || observationError"
      type="error"
      show-icon
      class="mb8"
    />
    <div class="reconcile-toolbar">
      <div class="reconcile-toolbar__status" role="status">
        <template v-if="latestRun">
          <span>最近一次检查</span>
          <a-tag :color="latestRun.status === 'failed' ? 'red' : 'blue'">{{
            jobStatuses[latestRun.status]
          }}</a-tag>
          <span>{{ formatTime(latestRun.finishedAt ?? latestRun.startedAt) }}</span>
        </template>
        <span v-else-if="stats">暂无检查记录</span>
      </div>
      <div class="reconcile-toolbar__actions">
        <a-tooltip
          title="分批核对系统记录和实际文件，标记缺失、未登记或内容不符的问题。检查不会删除文件，但异常文件可能无法下载。"
          :trigger="['hover', 'focus', 'click']"
        >
          <a-button aria-label="检查说明" type="text"><InfoCircleOutlined /></a-button>
        </a-tooltip>
        <RefreshButton label="刷新检查结果" :loading="loading" @click="refresh()" />
        <a-button
          v-if="canWrite"
          type="primary"
          aria-label="开始检查"
          :disabled="readDenied || !stats"
          :loading="busy || running"
          @click="scan"
          >开始检查</a-button
        >
      </div>
    </div>
    <a-tabs :active-key="activeTab" @update:active-key="(value) => (activeTab = String(value))">
      <a-tab-pane key="issues" tab="异常记录"
        ><a-form layout="inline" class="mb8"
          ><a-form-item label="异常状态"
            ><a-select
              :value="status"
              :options="statusOptions"
              style="width: 160px"
              @update:value="setStatus" /></a-form-item
        ></a-form>
        <ResourceTable
          :columns="visibleIssueColumns"
          :rows="issues"
          :loading="loading"
          :empty-text="emptyIssuesText"
          row-key="id"
          :scroll-x="840"
          ><template #bodyCell="{ column, record: row }">
            <template v-if="column.key === 'kind'">
              <div>{{ kinds[row.kind] }}</div>
              <div v-if="row.detail && row.detail !== row.kind" class="cell-secondary">
                {{ row.detail }}
              </div>
            </template>
            <template v-else-if="column.key === 'file'">
              <div v-if="row.fileId" class="file-identifier" :title="row.fileId">
                {{ row.fileId }}
              </div>
              <template v-else>
                <div>未登记</div>
                <div class="cell-secondary file-identifier" :title="row.id">{{ row.id }}</div>
              </template>
            </template>
            <template v-else-if="column.key === 'size'"
              ><div v-if="row.expectedSize !== null">
                记录 {{ formatFileSize(row.expectedSize) }}
              </div>
              <div v-if="row.actualSize !== null">实际 {{ formatFileSize(row.actualSize) }}</div>
              <span v-if="row.expectedSize === null && row.actualSize === null">—</span></template
            >
            <a-tag v-else-if="column.key === 'status'">{{ statuses[row.status] }}</a-tag
            ><template v-else-if="column.key === 'updatedAt'">{{
              formatTime(row.updatedAt)
            }}</template>
            <a-dropdown
              v-else-if="column.key === 'actions' && actions(row).length"
              :disabled="busy || running"
              :trigger="['click']"
              ><a-button size="small" type="link" :disabled="busy || running" aria-label="处理异常"
                >处理 <DownOutlined /></a-button
              ><template #overlay
                ><a-menu @click="({ key }) => handleCommand(key, row)"
                  ><a-menu-item v-for="action in actions(row)" :key="action">{{
                    actionDetails[action].label
                  }}</a-menu-item></a-menu
                ></template
              ></a-dropdown
            >
          </template></ResourceTable
        >
      </a-tab-pane>
      <a-tab-pane key="runs" tab="检查记录"
        ><ResourceTable
          :columns="runColumns"
          :rows="runs"
          :loading="loading"
          :empty-text="
            observationError ? '暂时无法读取检查记录' : stats ? '暂无检查记录' : '正在读取检查记录'
          "
          row-key="id"
          :scroll-x="910"
          ><template #bodyCell="{ column, record: row }"
            ><a-tag v-if="column.key === 'status'">{{ jobStatuses[row.status] }}</a-tag
            ><template v-else-if="column.key === 'startedAt'">{{
              formatTime(row.startedAt)
            }}</template
            ><template v-else-if="column.key === 'finishedAt'">{{
              formatTime(row.finishedAt)
            }}</template
            ><template v-else-if="column.key === 'errors'">{{
              row.errors.join('；') || '—'
            }}</template></template
          ></ResourceTable
        ></a-tab-pane
      >
    </a-tabs>
  </ResourceDrawer>
  <a-modal
    :open="modalOpen && canWrite"
    :title="actionDetails[form.action].label"
    :width="520"
    @cancel="cancelAction"
    ><p class="reconcile-effect">{{ actionDetails[form.action].effect }}</p>
    <a-alert v-if="error" :message="error" type="error" show-icon class="mb8" />
    <a-form :model="form" layout="vertical" :disabled="busy"
      ><a-form-item label="处理原因" required
        ><a-textarea
          v-model:value="form.reason"
          :rows="4"
          :maxlength="1000"
          placeholder="请填写处理原因" /></a-form-item></a-form
    ><template #footer
      ><a-button @click="cancelAction">取消</a-button
      ><a-button
        type="primary"
        :danger="form.action === 'quarantine'"
        :loading="busy"
        @click="submit"
        >{{ actionDetails[form.action].label }}</a-button
      ></template
    ></a-modal
  >
</template>

<style scoped>
.reconcile-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 8px;
}

.reconcile-toolbar__status,
.reconcile-toolbar__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.reconcile-toolbar__status {
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
}

.reconcile-toolbar__actions {
  margin-inline-start: auto;
}

.reconcile-effect {
  color: var(--workspace-text-secondary);
}

.cell-secondary {
  margin-top: 3px;
  color: var(--workspace-text-secondary);
  font-size: var(--workspace-font-size-caption);
  overflow-wrap: anywhere;
}

.file-identifier {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
