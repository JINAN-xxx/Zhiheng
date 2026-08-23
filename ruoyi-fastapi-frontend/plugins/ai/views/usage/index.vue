<template>
  <main class="usage-ledger-page" aria-label="用量记录">
    <WorkspaceToolbar subtitle="查看调用结果、消耗与费用">
      <template #actions>
        <a-button v-if="canQueryUsage" :loading="loading" @click="loadUsage">
          <template #icon><ReloadOutlined /></template>
          刷新
        </a-button>
      </template>
    </WorkspaceToolbar>

    <section v-if="canQueryUsage" class="usage-controls" aria-label="用量筛选">
      <a-select
        v-model:value="selectedApplicationId"
        show-search
        option-filter-prop="label"
        placeholder="选择应用"
        aria-label="选择应用"
        :options="applicationOptions"
        @change="handleApplicationChange"
      />
      <a-select
        v-model:value="query.operationType"
        allow-clear
        placeholder="全部类型"
        aria-label="调用类型"
        :options="operationOptions"
        @change="resetAndLoad"
      />
      <a-select
        v-model:value="query.status"
        allow-clear
        placeholder="全部状态"
        aria-label="调用状态"
        :options="statusOptions"
        @change="resetAndLoad"
      />
    </section>

    <p v-if="canQueryUsage && error && rows.length" class="usage-stale" role="status">
      刷新失败，当前数据可能已过期。最近成功：{{ formatTime(lastSuccessAt) }}
    </p>
    <WorkspaceState v-if="!canQueryUsage" kind="forbidden" title="无权查看用量记录" message="当前账号缺少应用查询权限。" />
    <WorkspaceState v-else-if="loading && !rows.length" kind="loading" />
    <WorkspaceState v-else-if="error && !rows.length" kind="error" :message="error" @retry="loadUsage" />
    <WorkspaceState v-else-if="!selectedApplicationId" kind="empty" title="暂无可查看应用" message="创建应用后可查看调用用量。" />
    <WorkspaceState v-else-if="!rows.length" kind="empty" title="暂无用量记录" message="公开 API 调用后会在这里形成台账。" />

    <section v-else class="usage-table-wrap">
      <ResourceTable
        :columns="columns"
        :rows="rows"
        :pagination="false"
        :scroll="{ x: 960 }"
        row-key="requestId"
      >
        <template #bodyCell="{ column, record: row }">
          <template v-if="column.key === 'startedAt'">{{ formatTime(row.startedAt) }}</template>
          <template v-else-if="column.key === 'operationType'">
            <a-tag :color="operationColor(operationMeta(row.operationType).type)">{{ operationMeta(row.operationType).label }}</a-tag>
          </template>
          <template v-else-if="column.key === 'tokens'">{{ row.inputTokens ?? '-' }} / {{ row.outputTokens ?? '-' }}</template>
          <template v-else-if="column.key === 'searchUnits'">{{ row.searchUnits ?? '-' }}</template>
          <template v-else-if="column.key === 'cost'">
            {{ formatCost(row.billableCost) }}
            <a-tag v-if="row.costEstimated" color="orange">估算</a-tag>
          </template>
          <template v-else-if="column.key === 'status'">{{ statusLabel(row.status) }}</template>
        </template>
      </ResourceTable>
      <a-pagination
        v-if="total > query.pageSize"
        v-model:current="query.pageNum"
        v-model:page-size="query.pageSize"
        :total="total"
        :page-size-options="['10', '20', '30', '50']"
        show-size-changer
        show-quick-jumper
        responsive
        @change="loadUsage"
      />
    </section>
  </main>
</template>

<script setup name="AiUsage">
import { ReloadOutlined } from '@ant-design/icons-vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import useUserStore from '@/store/modules/user'
import { listApplications, listApplicationUsage } from '../../api/application.js'
import { normalizeApplicationPage } from '../../utils/application.js'
import { hasPermission } from '../../utils/modelEngine.js'

const userStore = useUserStore()
const applications = ref([])
const selectedApplicationId = ref('')
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const lastSuccessAt = ref(null)
const query = reactive({ operationType: '', status: '', pageNum: 1, pageSize: 20 })
let requestGeneration = 0
const canQueryUsage = computed(() => (
  hasPermission(userStore.permissions || [], 'ai:application:list')
  && hasPermission(userStore.permissions || [], 'ai:application:query')
))

const applicationOptions = computed(() => applications.value.map((application) => ({
  label: `${application.applicationName} · ${application.applicationCode}`,
  value: application.applicationId,
})))
const operationOptions = [
  { label: '对话', value: 'chat' },
  { label: '向量', value: 'embedding' },
  { label: '排序', value: 'rerank' },
]
const statusOptions = [
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
  { label: '已放弃', value: 'abandoned' },
]
const columns = [
  { title: '开始时间', key: 'startedAt', width: 170 },
  { title: '类型', key: 'operationType', width: 110 },
  { title: '模型', dataIndex: 'modelCode', key: 'modelCode', width: 170, ellipsis: true },
  { title: '令牌', key: 'tokens', width: 150 },
  { title: '检索单元', key: 'searchUnits', width: 120, mobileHidden: true },
  { title: '费用', key: 'cost', width: 130, mobileHidden: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 110 },
]

const operationMeta = (operationType) => ({
  chat: { label: '对话', type: 'primary' },
  embedding: { label: '向量', type: 'success' },
  rerank: { label: '排序', type: 'warning' },
}[operationType] || { label: operationType || '-', type: 'info' })
const operationColor = (type) => ({ primary: 'blue', success: 'green', warning: 'orange' }[type] || 'default')

async function loadApplications() {
  if (!canQueryUsage.value) return
  const page = normalizeApplicationPage(await listApplications({ pageNum: 1, pageSize: 100 }))
  applications.value = page.rows
  if (!selectedApplicationId.value && page.rows.length) {
    selectedApplicationId.value = page.rows[0].applicationId
  }
}

async function loadUsage() {
  if (!canQueryUsage.value || !selectedApplicationId.value) return
  const generation = ++requestGeneration
  const applicationId = selectedApplicationId.value
  loading.value = true
  error.value = ''
  try {
    const response = await listApplicationUsage(applicationId, {
      pageNum: query.pageNum,
      pageSize: query.pageSize,
      operationType: query.operationType || undefined,
      status: query.status || undefined,
    })
    if (generation !== requestGeneration || applicationId !== selectedApplicationId.value) return
    const page = response?.data || response || {}
    rows.value = Array.isArray(page.rows) ? page.rows : []
    total.value = Number.isInteger(page.total) ? page.total : rows.value.length
    lastSuccessAt.value = Date.now()
  } catch (reason) {
    if (generation === requestGeneration) error.value = reason?.message || '用量台账加载失败'
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

async function handleApplicationChange() {
  requestGeneration += 1
  rows.value = []
  total.value = 0
  query.pageNum = 1
  await loadUsage()
}

function resetAndLoad() {
  query.pageNum = 1
  loadUsage()
}

function formatTime(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

const statusLabel = (status) => ({
  completed: '成功', failed: '失败', cancelled: '已取消', abandoned: '已放弃', started: '处理中',
}[status] || status || '-')

function formatCost(value) {
  return value == null ? '-' : Number(value).toFixed(6)
}

onMounted(async () => {
  try {
    await loadApplications()
    await loadUsage()
  } catch (reason) {
    error.value = reason?.message || '应用列表加载失败'
  }
})

onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped lang="scss">
.usage-ledger-page { min-width: 0; }
.usage-controls { display: grid; grid-template-columns: minmax(240px, 1fr) 180px 180px; gap: 10px; margin: 16px 0; padding: 14px; border: 1px solid var(--workspace-border); border-radius: var(--workspace-radius-md); background: var(--workspace-surface); }
.usage-stale { margin: 0 0 10px; padding: 9px 12px; border-radius: var(--workspace-radius-sm); color: var(--workspace-warning); background: var(--workspace-warning-soft); font-size: 12px; }
.usage-table-wrap { overflow: hidden; border: 1px solid var(--workspace-border); border-radius: var(--workspace-radius-md); background: var(--workspace-surface); }
.usage-table-wrap :deep(.ant-pagination) { justify-content: flex-end; margin: 14px; }
@media (max-width: 760px) {
  .usage-controls { grid-template-columns: 1fr; }
  .usage-table-wrap :deep(.ant-pagination) { justify-content: center; }
  .usage-table-wrap :deep(.ant-pagination-options) { display: none; }
}
</style>
