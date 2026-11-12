<!-- Adapted from original system/file/index.vue, MIT licensed. -->
<script setup lang="ts">
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { inject, computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Pagination as APagination,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  BellOutlined,
  DeleteFilled,
  DeleteOutlined,
  DownOutlined,
  FieldTimeOutlined,
  FilterOutlined,
  LockOutlined,
  PartitionOutlined,
  RollbackOutlined,
  SwapOutlined
} from '@ant-design/icons-vue'
import { adminReadCacheKey } from '../../admin/public.ts'
import WorkspacePage from '../../../shared/components/WorkspacePage.vue'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  changeFileState,
  downloadFile,
  emptyQuery,
  findFileUsers,
  getFileStats,
  listFiles
} from '../api/index.ts'
import type { FileStats, FileUser, ManagedFile } from '../api/index.ts'
import { formatFileSize, isoDateTime, saveBlob } from '../presentation.ts'
import FileAclDrawer from '../components/FileAclDrawer.vue'
import FileAuditDrawer from '../components/FileAuditDrawer.vue'
import FileDetailDialog from '../components/FileDetailDialog.vue'
import FileReferenceDrawer from '../components/FileReferenceDrawer.vue'
import FileReconcileDrawer from '../components/FileReconcileDrawer.vue'
import FileRetentionPolicyDrawer from '../components/FileRetentionPolicyDrawer.vue'
import FileRetentionReminderDrawer from '../components/FileRetentionReminderDrawer.vue'
import FileSearchForm from '../components/FileSearchForm.vue'
import FileStatistics from '../components/FileStatistics.vue'
import FileTable from '../components/FileTable.vue'
import FileTransferDialog from '../components/FileTransferDialog.vue'
import { canManageFile } from '../access.ts'
const canReadStorage = computed(() =>
  hasAnyPermission(identity.value, 'ai:storage:read', 'ai:storage:write')
)
const canWriteStorage = computed(() => hasAnyPermission(identity.value, 'ai:storage:write'))
interface StorageReadState {
  query: ReturnType<typeof emptyQuery>
  dates: [string, string] | null
  showSearch: boolean
  showStatistics: boolean
  files: ManagedFile[]
  stats: FileStats | null
  users: FileUser[]
  usersReady: boolean
  total: number
  hasData: boolean
}
const readCache = inject(adminReadCacheKey, null)
const savedStorage = readCache?.getResource<StorageReadState>('storage-view')
const saved = savedStorage?.value
const queryParams = ref(saved?.query ?? emptyQuery()),
  dateRange = ref<[string, string] | null>(saved?.dates ?? null),
  showSearch = ref(saved?.showSearch ?? false),
  showStatistics = ref(saved?.showStatistics ?? false)
const files = ref<ManagedFile[]>(saved?.hasData ? saved.files : []),
  stats = ref<FileStats | null>(saved?.hasData ? saved.stats : null),
  users = ref<FileUser[]>(saved?.users ?? []),
  selected = ref<ManagedFile[]>([])
const total = ref(saved?.hasData ? saved.total : 0),
  loading = ref(!saved?.hasData),
  error = ref(''),
  busyIds = ref<string[]>([]),
  notice = ref('')
const router = useRouter()
const detail = ref<InstanceType<typeof FileDetailDialog> | null>(null),
  references = ref<InstanceType<typeof FileReferenceDrawer> | null>(null),
  acl = ref<InstanceType<typeof FileAclDrawer> | null>(null),
  transfer = ref<InstanceType<typeof FileTransferDialog> | null>(null),
  audit = ref<InstanceType<typeof FileAuditDrawer> | null>(null),
  policy = ref<InstanceType<typeof FileRetentionPolicyDrawer> | null>(null),
  reminders = ref<InstanceType<typeof FileRetentionReminderDrawer> | null>(null),
  reconcile = ref<InstanceType<typeof FileReconcileDrawer> | null>(null)
const scope = new RequestScope(),
  writes = new RequestScope(),
  userScope = new RequestScope(),
  { confirm } = useConfirmation()
const activeSelected = computed(() =>
  selected.value.filter(
    (file) => file.status === 'active' && !file.sourceFileId && canManageFile(identity.value, file)
  )
)
const hasActiveSelection = computed(
  () => selected.value.length > 0 && activeSelected.value.length === selected.value.length
)
const mutationBusy = computed(() => busyIds.value.length > 0)
let loadedStorage = saved?.hasData ?? false,
  usersReady = saved?.usersReady ?? false,
  loadedAt = savedStorage?.capturedAt ?? 0
let storageStamp = readCache?.stamp()
const storageQuery = () =>
  JSON.stringify({
    ...queryParams.value,
    createdFrom: dateRange.value?.[0] ?? '',
    createdTo: dateRange.value?.[1] ?? ''
  })
let loadedQuery = loadedStorage ? storageQuery() : ''
function rememberStorage(): void {
  if (!readCache || !storageStamp) return
  const valid = loadedStorage && loadedQuery === storageQuery()
  readCache.setResource(
    'storage-view',
    {
      query: queryParams.value,
      dates: dateRange.value,
      showSearch: showSearch.value,
      showStatistics: showStatistics.value,
      files: valid ? files.value : [],
      stats: valid ? stats.value : null,
      users: users.value,
      usersReady,
      total: valid ? total.value : 0,
      hasData: valid
    },
    storageStamp,
    valid ? loadedAt : 0
  )
}

async function load(): Promise<void> {
  const context = scope.replace()
  const stamp = readCache?.stamp(),
    queryKey = storageQuery()
  loading.value = true
  error.value = ''
  const query = {
    ...queryParams.value,
    createdFrom: dateRange.value ? (isoDateTime(dateRange.value[0]) ?? '') : '',
    createdTo: dateRange.value ? (isoDateTime(dateRange.value[1]) ?? '') : ''
  }
  try {
    const [page, counts] = await Promise.all([
      listFiles(query, context.signal),
      getFileStats(query, context.signal)
    ])
    if (context.isCurrent()) {
      files.value = page.rows
      total.value = page.total
      stats.value = counts
      loadedStorage = true
      loadedQuery = queryKey
      loadedAt = Date.now()
      storageStamp = stamp
      rememberStorage()
      const ids = new Set(selected.value.map((file) => file.fileId))
      selected.value = page.rows.filter(
        (file) =>
          ids.has(file.fileId) &&
          !file.sourceFileId &&
          file.status !== 'purged' &&
          canManageFile(identity.value, file)
      )
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      if (reason instanceof ApiError && [401, 403, 404].includes(reason.status)) {
        files.value = []
        stats.value = null
        total.value = 0
        selected.value = []
      }
      loadedAt = 0
      error.value = errorMessage(reason)
    }
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function search(): void {
  selected.value = []
  queryParams.value.pageNum = 1
  void load()
}
function reset(): void {
  const status = queryParams.value.status
  queryParams.value = emptyQuery()
  queryParams.value.status = status
  dateRange.value = null
  selected.value = []
  void load()
}
function switchView(status: 'active' | 'deleted'): void {
  if (queryParams.value.status === status) return
  queryParams.value.status = status
  search()
}
function changePage(): void {
  selected.value = []
  void load()
}
async function download(file: ManagedFile): Promise<void> {
  if (busyIds.value.includes(file.fileId)) return
  const context = writes.current()
  busyIds.value.push(file.fileId)
  try {
    const blob = await downloadFile(file.fileId, context.signal)
    if (context.isCurrent()) saveBlob(blob, file.originalName)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) busyIds.value = busyIds.value.filter((id) => id !== file.fileId)
  }
}
async function change(action: 'delete' | 'restore' | 'purge', row?: ManagedFile): Promise<void> {
  if (mutationBusy.value || loading.value) return
  const targets = (row ? [row] : selected.value).map((file) => ({ ...file }))
  if (
    !targets.length ||
    targets.some(
      (file) =>
        !canManageFile(identity.value, file) ||
        file.sourceFileId ||
        (action === 'delete' && (file.status !== 'active' || file.referenceCount > 0)) ||
        (action === 'restore' && file.status !== 'deleted') ||
        (action === 'purge' &&
          (!['deleted', 'purging'].includes(file.status) || file.referenceCount > 0))
    )
  )
    return
  const context = writes.replace(),
    labels = { delete: '移入回收站', restore: '恢复', purge: '永久清理' }
  busyIds.value = targets.map((file) => file.fileId)
  let changed = 0
  try {
    if (
      !(await confirm(
        `是否${labels[action]}所选${targets.length}个文件？${action === 'purge' ? '清理后无法恢复。' : ''}`
      )) ||
      !context.isCurrent()
    )
      return
    readCache?.invalidate()
    for (const file of targets) {
      await changeFileState(file, action, context.signal)
      if (!context.isCurrent()) return
      changed += 1
      selected.value = selected.value.filter((selected) => selected.fileId !== file.fileId)
    }
    notice.value = `已${labels[action]}${changed}个文件`
    await load()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      const message = `已处理${changed}项；${errorMessage(reason)}。请刷新核对剩余文件状态。`
      await load()
      error.value = message
    }
  } finally {
    if (context.isCurrent()) busyIds.value = []
  }
}
onMounted(() => {
  if (!saved?.hasData || !readCache?.isFresh(savedStorage)) void load()
  if (usersReady && readCache?.isFresh(savedStorage)) return
  const context = userScope.replace()
  void findFileUsers('', context.signal)
    .then((values) => {
      if (context.isCurrent()) {
        users.value = values
        usersReady = true
      }
    })
    .catch((reason: unknown) => {
      if (context.isCurrent() && !isAborted(reason)) {
        if (reason instanceof ApiError && [401, 403, 404].includes(reason.status)) {
          users.value = []
          usersReady = false
          readCache?.removeResource('storage-view')
        }
        error.value = errorMessage(reason)
      }
    })
})
onBeforeUnmount(() => {
  rememberStorage()
  scope.dispose()
  writes.dispose()
  userScope.dispose()
})
</script>
<template>
  <WorkspacePage title="存储管理" subtitle="管理可访问文件与平台保留规则" class="files-page">
    <template #actions>
      <a-button aria-label="返回知识库" @click="router.push('/knowledge')"
        ><ArrowLeftOutlined />返回知识库</a-button
      >
      <a-dropdown :trigger="['click']">
        <a-button aria-label="高级管理">高级管理<DownOutlined /></a-button>
        <template #overlay>
          <a-menu>
            <a-menu-item
              key="statistics"
              aria-label="详细统计"
              @click="showStatistics = !showStatistics"
            >
              <BarChartOutlined />详细统计
            </a-menu-item>
            <a-menu-item
              v-if="canReadStorage"
              key="policy"
              aria-label="保留策略"
              @click="policy?.open()"
            >
              <FieldTimeOutlined />保留策略
            </a-menu-item>
            <a-menu-item key="reminders" aria-label="保留提醒" @click="reminders?.open()">
              <BellOutlined />保留提醒
            </a-menu-item>
            <a-menu-item
              v-if="canReadStorage"
              key="reconcile"
              aria-label="存储对账"
              @click="reconcile?.open()"
            >
              <PartitionOutlined />存储对账
            </a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
    </template>
    <a-alert v-if="error" :message="error" type="error" show-icon class="mb8" /><a-alert
      v-if="notice"
      :message="notice"
      type="success"
      closable
      class="mb8"
      @close="notice = ''"
    />
    <FileStatistics v-if="showStatistics && stats" :stats="stats" />
    <section class="file-resources" aria-label="文件资源">
      <div class="file-resources__heading">
        <div class="file-resources__overview">
          <h2>{{ queryParams.status === 'deleted' ? '回收站' : '可访问文件' }}</h2>
          <div v-if="stats" role="region" aria-label="当前查询概览" class="file-summary">
            当前查询 {{ total }} 个文件 · {{ formatFileSize(stats.totalSize) }}
          </div>
        </div>
        <div class="file-resources__tools">
          <a-tooltip title="展开更多筛选条件"
            ><a-button
              aria-label="高级筛选"
              :aria-expanded="showSearch"
              @click="showSearch = !showSearch"
              ><FilterOutlined />高级筛选</a-button
            ></a-tooltip
          ><RefreshButton label="刷新文件" :loading="loading" @click="load" />
        </div>
      </div>
      <div class="file-view-tabs" role="tablist" aria-label="文件视图">
        <button
          type="button"
          role="tab"
          :aria-selected="queryParams.status === 'active'"
          @click="switchView('active')"
        >
          文件
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="queryParams.status === 'deleted'"
          @click="switchView('deleted')"
        >
          回收站
        </button>
      </div>
      <FileSearchForm
        v-model:query-params="queryParams"
        v-model:date-range="dateRange"
        :show="showSearch"
        @query="search"
        @reset="reset"
      />
      <div v-if="selected.length" role="region" aria-label="批量文件操作" class="table-toolbar mb8">
        <span class="file-selection">已选 {{ selected.length }} 项</span>
        <a-button
          v-if="selected.some((file) => file.status === 'deleted')"
          aria-label="恢复"
          :disabled="
            !selected.length ||
            selected.some(
              (file) =>
                file.status !== 'deleted' || file.sourceFileId || !canManageFile(identity, file)
            ) ||
            mutationBusy ||
            loading
          "
          @click="change('restore')"
          ><RollbackOutlined />恢复</a-button
        >
        <a-button
          v-if="selected.some((file) => ['deleted', 'purging'].includes(file.status))"
          aria-label="永久清理"
          danger
          :disabled="
            !selected.length ||
            selected.some(
              (file) =>
                !['deleted', 'purging'].includes(file.status) ||
                file.sourceFileId ||
                file.referenceCount > 0 ||
                !canManageFile(identity, file)
            ) ||
            mutationBusy ||
            loading
          "
          @click="change('purge')"
          ><DeleteFilled />永久清理</a-button
        >
        <a-button
          v-if="selected.some((file) => file.status === 'active')"
          aria-label="移入回收站"
          danger
          :disabled="
            !hasActiveSelection ||
            activeSelected.some((file) => file.referenceCount > 0) ||
            mutationBusy ||
            loading
          "
          @click="change('delete')"
          ><DeleteOutlined />移入回收站</a-button
        >
        <a-dropdown v-if="hasActiveSelection" :trigger="['click']">
          <a-button aria-label="更多批量操作" :disabled="mutationBusy || loading"
            >更多<DownOutlined
          /></a-button>
          <template #overlay>
            <a-menu>
              <a-menu-item
                key="acl"
                :disabled="mutationBusy || loading"
                @click="acl?.open(activeSelected)"
                ><LockOutlined />授权</a-menu-item
              >
              <a-menu-item
                key="transfer"
                :disabled="mutationBusy || loading"
                @click="transfer?.open(activeSelected)"
                ><SwapOutlined />转移</a-menu-item
              >
            </a-menu>
          </template>
        </a-dropdown>
      </div>
      <FileTable
        :file-list="files"
        :users="users"
        :loading="loading"
        :busy-ids="loading ? files.map((file) => file.fileId) : busyIds"
        :selected-ids="selected.map((file) => file.fileId)"
        @selection-change="selected = $event"
        @view="detail?.open($event)"
        @download="download"
        @reference="canManageFile(identity, $event) && references?.open($event)"
        @acl="canManageFile(identity, $event) && acl?.open([$event])"
        @transfer="canManageFile(identity, $event) && transfer?.open([$event])"
        @audit="canManageFile(identity, $event) && audit?.open($event)"
        @delete="change('delete', $event)"
        @restore="change('restore', $event)"
        @purge="change('purge', $event)"
      />
      <a-pagination
        v-if="total > 0"
        v-model:current="queryParams.pageNum"
        v-model:page-size="queryParams.pageSize"
        :total="total"
        show-size-changer
        @change="changePage"
      />
    </section>
    <FileDetailDialog
      ref="detail"
      :users="users"
      @reference="references?.open($event)"
      @refresh="load"
    /><FileReferenceDrawer ref="references" /><FileAclDrawer
      ref="acl"
      :users="users"
      @refresh="load"
    /><FileTransferDialog ref="transfer" @refresh="load" /><FileAuditDrawer
      ref="audit"
      :users="users"
    /><FileRetentionPolicyDrawer
      ref="policy"
      :can-read="canReadStorage"
      :can-write="canWriteStorage"
    /><FileRetentionReminderDrawer
      ref="reminders"
      :users="users"
      :can-run-jobs="canWriteStorage"
      @refresh="load"
    /><FileReconcileDrawer
      ref="reconcile"
      :can-read="canReadStorage"
      :can-write="canWriteStorage"
      @refresh="load"
    />
  </WorkspacePage>
</template>
<style scoped>
.files-page {
  color: var(--color-text);
}
.file-resources {
  min-width: 0;
  padding: 20px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
}
.file-resources__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.file-resources__heading h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 500;
}
.file-resources__heading h2 span {
  margin-left: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 400;
}
.file-resources__overview {
  min-width: 0;
}
.file-summary {
  margin-top: 5px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.file-view-tabs {
  display: flex;
  gap: 20px;
  margin-bottom: 18px;
  border-bottom: 1px solid var(--gray-150);
}
.file-view-tabs button {
  padding: 0 0 10px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}
.file-view-tabs button[aria-selected='true'] {
  border-bottom-color: var(--main-700);
  color: var(--main-700);
}
.file-resources__tools {
  display: flex;
  gap: 8px;
  flex: 0 0 auto;
}
.table-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.file-selection {
  margin-right: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
:deep(.ant-pagination) {
  margin-top: 18px;
  text-align: right;
}
@media (max-width: 767px) {
  .file-resources {
    padding: 16px;
  }
  .file-resources__heading {
    flex-wrap: wrap;
  }
  .file-selection {
    flex-basis: 100%;
  }
}
</style>
