<!-- Adapted from original system/file/index.vue, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Pagination as APagination,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  BellOutlined,
  DeleteFilled,
  DeleteOutlined,
  FieldTimeOutlined,
  FilterOutlined,
  LockOutlined,
  PartitionOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SwapOutlined
} from '@ant-design/icons-vue'
import WorkspacePage from '../../../shared/components/WorkspacePage.vue'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  changeFileState,
  downloadFile,
  emptyQuery,
  findFileUsers,
  getFileStats,
  listFiles
} from '../api/index.ts'
import type { FileStats, FileUser, ManagedFile } from '../api/index.ts'
import { isoDateTime, saveBlob } from '../presentation.ts'
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
const queryParams = ref(emptyQuery()),
  dateRange = ref<[string, string] | null>(null),
  showSearch = ref(true)
const files = ref<ManagedFile[]>([]),
  stats = ref<FileStats | null>(null),
  users = ref<FileUser[]>([]),
  selected = ref<ManagedFile[]>([])
const total = ref(0),
  loading = ref(false),
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
async function load(): Promise<void> {
  const context = scope.replace()
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
      selected.value = []
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function search(): void {
  queryParams.value.pageNum = 1
  void load()
}
function reset(): void {
  queryParams.value = emptyQuery()
  dateRange.value = null
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
        !canManageFile(identity.value, file) || file.sourceFileId || file.status === 'purged'
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
    for (const file of targets) {
      await changeFileState(file, action, context.signal)
      if (!context.isCurrent()) return
      changed += 1
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
  void load()
  const context = userScope.replace()
  void findFileUsers('', context.signal)
    .then((values) => {
      if (context.isCurrent()) users.value = values
    })
    .catch((reason: unknown) => {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    })
})
onBeforeUnmount(() => {
  scope.dispose()
  writes.dispose()
  userScope.dispose()
})
</script>
<template>
  <WorkspacePage
    title="存储管理"
    subtitle="管理知识文档与对话附件的存储、权限及保留规则"
    class="files-page"
  >
    <template #actions>
      <a-button aria-label="返回知识库" @click="router.push('/knowledge')"
        ><ArrowLeftOutlined />返回知识库</a-button
      >
      <a-button v-if="canReadStorage" aria-label="保留策略" @click="policy?.open()"
        ><FieldTimeOutlined />保留策略</a-button
      >
      <a-button v-if="canReadStorage" aria-label="存储对账" @click="reconcile?.open()"
        ><PartitionOutlined />存储对账</a-button
      >
      <a-button aria-label="保留提醒" @click="reminders?.open()"><BellOutlined />保留提醒</a-button>
    </template>
    <a-alert v-if="error" :message="error" type="error" show-icon class="mb8" /><a-alert
      v-if="notice"
      :message="notice"
      type="success"
      closable
      class="mb8"
      @close="notice = ''"
    />
    <FileStatistics v-if="stats" :stats="stats" />
    <section class="file-resources" aria-label="文件资源">
      <div class="file-resources__heading">
        <h2>
          文件列表 <span>{{ total }}</span>
        </h2>
        <div class="file-resources__tools">
          <a-tooltip title="显示或隐藏筛选"
            ><a-button
              aria-label="显示或隐藏筛选"
              :aria-expanded="showSearch"
              @click="showSearch = !showSearch"
              ><FilterOutlined /></a-button></a-tooltip
          ><a-tooltip title="刷新"
            ><a-button aria-label="刷新文件" :loading="loading" @click="load"
              ><ReloadOutlined /></a-button
          ></a-tooltip>
        </div>
      </div>
      <FileSearchForm
        v-model:query-params="queryParams"
        v-model:date-range="dateRange"
        :show="showSearch"
        @query="search"
        @reset="reset"
      />
      <div class="table-toolbar mb8">
        <span class="file-selection">已选 {{ selected.length }} 项</span>
        <a-button
          :disabled="!hasActiveSelection || mutationBusy || loading"
          @click="acl?.open(activeSelected)"
          ><LockOutlined />授权</a-button
        >
        <a-button
          :disabled="!hasActiveSelection || mutationBusy || loading"
          @click="transfer?.open(activeSelected)"
          ><SwapOutlined />转移</a-button
        >
        <a-button
          :disabled="
            !selected.length ||
            selected.some((file) => file.status !== 'deleted' || !canManageFile(identity, file)) ||
            mutationBusy ||
            loading
          "
          @click="change('restore')"
          ><RollbackOutlined />恢复</a-button
        >
        <a-button
          danger
          :disabled="
            !selected.length ||
            selected.some(
              (file) =>
                !['deleted', 'purging'].includes(file.status) ||
                file.referenceCount > 0 ||
                !canManageFile(identity, file)
            ) ||
            mutationBusy ||
            loading
          "
          @click="change('purge')"
          ><DeleteFilled />清理</a-button
        >
        <a-button
          danger
          :disabled="
            !hasActiveSelection ||
            activeSelected.some((file) => file.referenceCount > 0) ||
            mutationBusy ||
            loading
          "
          @click="change('delete')"
          ><DeleteOutlined />删除</a-button
        >
      </div>
      <FileTable
        :file-list="files"
        :users="users"
        :loading="loading"
        :busy-ids="loading ? files.map((file) => file.fileId) : busyIds"
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
        @change="load"
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
.file-resources__tools {
  display: flex;
  gap: 8px;
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
  .file-selection {
    flex-basis: 100%;
  }
}
</style>
