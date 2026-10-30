<!-- Adapted from original FileReconcileDrawer, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Select as ASelect,
  TabPane as ATabPane,
  Tabs as ATabs,
  Tag as ATag,
  Textarea as ATextarea
} from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { ReloadOutlined, SettingOutlined } from '@ant-design/icons-vue'
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
const stats = reactive({ open: 0, resolved: 0, ignored: 0, quarantined: 0 })
const form = reactive({ action: 'ignore' as Action, reason: '', originalName: '' })
type Action = 'ignore' | 'quarantine' | 'register' | 'repair'
const actionLabels: Record<Action, string> = {
  ignore: '忽略异常',
  quarantine: '隔离文件',
  register: '登记文件',
  repair: '复验修复'
}
const kinds: Record<ReconcileIssue['kind'], string> = {
  missing: '物理文件缺失',
  orphan: '孤立物理文件',
  size_or_hash: '大小或摘要不一致',
  invalidkey: '不安全目录项'
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
const issueColumns: TableColumnsType<ReconcileIssue> = [
  { title: '异常类型', key: 'kind', width: 150 },
  { title: '文件', key: 'file', width: 230 },
  { title: '大小', key: 'size', width: 150 },
  { title: '状态', key: 'status', width: 100 },
  { title: '异常说明', dataIndex: 'detail', key: 'detail', width: 200 },
  { title: '最近发现', key: 'updatedAt', width: 180 },
  { title: '操作', key: 'actions', width: 70 }
]
const visibleIssueColumns = computed(() =>
  issueColumns.filter((column) => column.key !== 'actions' || props.canWrite)
)
const runColumns: TableColumnsType<FileJob> = [
  { title: '任务状态', key: 'status', width: 100 },
  { title: '检查对象', dataIndex: 'scanned', key: 'scanned', width: 100 },
  { title: '变更数量', dataIndex: 'changed', key: 'changed', width: 100 },
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
    Object.assign(stats, counts)
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
        Object.assign(stats, { open: 0, resolved: 0, ignored: 0, quarantined: 0 })
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
  if (issue.status === 'quarantined' && issue.fileId) return ['repair']
  if (issue.status !== 'open') return []
  if (issue.kind === 'orphan') return ['ignore', 'quarantine', 'register']
  if (issue.fileId) return ['ignore', 'quarantine', 'repair']
  return ['ignore']
}
function handleCommand(value: unknown, issue: ReconcileIssue): void {
  if (busy.value || running.value || !actions(issue).includes(value as Action)) return
  current.value = { ...issue }
  Object.assign(form, { action: value, reason: '', originalName: '' })
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
    if (job.status === 'failed') throw new Error(job.errors.join('；') || '对账失败')
  })
}
async function submit(): Promise<void> {
  if (!current.value || !props.canWrite) return
  const target = { ...current.value },
    action = form.action,
    reason = form.reason.trim(),
    name = form.originalName.trim()
  if (
    !reason ||
    [...reason].length > 500 ||
    (action === 'register' && (!name || [...name].length > 255))
  ) {
    error.value = '请填写不超过500字的处理依据；登记时需提供255字以内的文件名'
    return
  }
  await command(async (signal) => {
    await resolveReconcileIssue(target, action, reason, name, signal)
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
  <ResourceDrawer :open="visible" title="文件存储对账" width="88%" @close="close">
    <a-alert
      v-if="error || observationError"
      :message="error || observationError"
      type="error"
      show-icon
      class="mb8"
    />
    <a-alert
      message="对账检查登记信息与实际文件的大小和摘要。隔离文件不可下载；修复仅复验已恢复的真实内容。每次分批检查，连续运行会继续扫描。"
      type="info"
      show-icon
      class="mb12"
    />
    <div class="reconcile-summary">
      <div
        v-for="key in ['open', 'resolved', 'ignored', 'quarantined'] as const"
        :key="key"
        class="summary-card"
        :class="key === 'open' ? 'summary-card--danger' : 'summary-card--quarantine'"
      >
        <div class="summary-card__label">{{ statuses[key] }}</div>
        <div class="summary-card__value">{{ stats[key] }}</div>
      </div>
      <div class="summary-card summary-card--run">
        <div class="summary-card__label">最近任务</div>
        <div class="summary-card__status">
          <a-tag v-if="runs[0]" :color="runs[0].status === 'failed' ? 'red' : 'blue'">{{
            jobStatuses[runs[0].status]
          }}</a-tag
          ><span v-else>尚未运行</span>
        </div>
        <div class="summary-card__hint">
          {{ runs[0] ? formatTime(runs[0].startedAt) : '可手动开始检查' }}
        </div>
      </div>
    </div>
    <div class="reconcile-toolbar">
      <span class="reconcile-toolbar__tip">异常和任务列表分别显示最近100条、50条记录</span>
      <div class="reconcile-toolbar__actions">
        <a-button
          v-if="canWrite"
          type="primary"
          aria-label="开始对账"
          :disabled="readDenied"
          :loading="busy || running"
          @click="scan"
          ><ReloadOutlined />开始对账</a-button
        ><a-button :loading="loading" @click="refresh()">刷新</a-button>
      </div>
    </div>
    <a-tabs :active-key="activeTab" @update:active-key="(value) => (activeTab = String(value))">
      <a-tab-pane key="issues" tab="异常列表"
        ><a-form layout="inline" class="mb8"
          ><a-form-item label="异常状态"
            ><a-select
              :value="status"
              :options="Object.entries(statuses).map(([value, label]) => ({ value, label }))"
              style="width: 160px"
              @update:value="setStatus" /></a-form-item
        ></a-form>
        <ResourceTable
          :columns="visibleIssueColumns"
          :rows="issues"
          :loading="loading"
          row-key="id"
          :scroll-x="1200"
          ><template #bodyCell="{ column, record: row }">
            <template v-if="column.key === 'kind'">{{ kinds[row.kind] }}</template
            ><template v-else-if="column.key === 'file'"
              ><div>{{ row.fileId ?? '未登记文件' }}</div>
              <div class="cell-secondary">异常 {{ row.id }}</div></template
            >
            <template v-else-if="column.key === 'size'"
              ><div>
                预期：{{ row.expectedSize === null ? '—' : formatFileSize(row.expectedSize) }}
              </div>
              <div class="cell-secondary">
                实际：{{ row.actualSize === null ? '—' : formatFileSize(row.actualSize) }}
              </div></template
            >
            <a-tag v-else-if="column.key === 'status'">{{ statuses[row.status] }}</a-tag
            ><template v-else-if="column.key === 'updatedAt'">{{
              formatTime(row.updatedAt)
            }}</template>
            <a-dropdown
              v-else-if="column.key === 'actions' && actions(row).length"
              :disabled="busy || running"
              :trigger="['click']"
              ><a-button type="link" :disabled="busy || running" aria-label="处理对账异常"
                ><SettingOutlined /></a-button
              ><template #overlay
                ><a-menu @click="({ key }) => handleCommand(key, row)"
                  ><a-menu-item v-for="action in actions(row)" :key="action">{{
                    actionLabels[action]
                  }}</a-menu-item></a-menu
                ></template
              ></a-dropdown
            >
          </template></ResourceTable
        >
      </a-tab-pane>
      <a-tab-pane key="runs" tab="任务记录"
        ><ResourceTable
          :columns="runColumns"
          :rows="runs"
          :loading="loading"
          row-key="id"
          :scroll-x="1000"
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
    :title="actionLabels[form.action]"
    :width="520"
    @cancel="modalOpen = false"
    ><a-form :model="form" layout="vertical" :disabled="busy"
      ><a-form-item v-if="form.action === 'register'" label="原始文件名"
        ><a-input v-model:value="form.originalName" :maxlength="510" /></a-form-item
      ><a-form-item label="处理原因"
        ><a-textarea
          v-model:value="form.reason"
          :rows="4"
          :maxlength="1000"
          placeholder="请填写处理依据" /></a-form-item></a-form
    ><template #footer
      ><a-button @click="modalOpen = false">取消</a-button
      ><a-button
        type="primary"
        :danger="form.action === 'quarantine'"
        :loading="busy"
        @click="submit"
        >确定</a-button
      ></template
    ></a-modal
  >
</template>

<style scoped>
.reconcile-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  position: relative;
  overflow: hidden;
  min-height: 112px;
  padding: 16px 18px;
  border: 1px solid var(--workspace-border);
  border-radius: 8px;
  background: var(--workspace-surface);
}

.summary-card--danger {
  --card-color: #cf1322;
}

.summary-card--critical {
  --card-color: #b42318;
}

.summary-card--warning {
  --card-color: #d48806;
}

.summary-card--quarantine {
  --card-color: #7f56d9;
}

.summary-card--run {
  --card-color: var(--workspace-main);
}

.summary-card__label {
  color: var(--workspace-text-secondary);
  font-size: 14px;
}

.summary-card__value {
  margin-top: 7px;
  color: var(--workspace-text);
  font-size: 23px;
  font-weight: 500;
  line-height: 34px;
}

.summary-card__status {
  display: flex;
  align-items: center;
  height: 41px;
}

.summary-card__hint {
  overflow: hidden;
  color: var(--workspace-text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reconcile-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 10px 14px;
  border-radius: 6px;
  background: var(--workspace-surface-muted);
}

.reconcile-toolbar__tip {
  color: var(--workspace-text-secondary);
  font-size: 13px;
}

.reconcile-toolbar__actions {
  display: flex;
  align-items: center;
  white-space: nowrap;
}

.reconcile-action-trigger {
  display: inline-flex;
  align-items: center;
}

.cell-secondary {
  overflow: hidden;
  margin-top: 3px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1400px) {
  .reconcile-summary {
    grid-template-columns: repeat(3, minmax(160px, 1fr));
  }
}
</style>
<style scoped>
@media (max-width: 767px) {
  .reconcile-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .summary-card {
    min-width: 0;
    padding: 12px;
  }
  .reconcile-toolbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }
  .reconcile-toolbar__actions {
    flex-wrap: wrap;
    white-space: normal;
    gap: 8px;
  }
}
@media (max-width: 359px) {
  .reconcile-summary {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
