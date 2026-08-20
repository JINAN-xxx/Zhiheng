<template>
  <main class="usage-ledger-page">
    <WorkspaceToolbar title="用量台账" subtitle="Usage Ledger">
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="loadUsage">刷新</el-button>
      </template>
    </WorkspaceToolbar>

    <section class="usage-controls" aria-label="用量筛选">
      <el-select
        v-model="selectedApplicationId"
        filterable
        placeholder="选择应用"
        aria-label="选择应用"
        @change="handleApplicationChange"
      >
        <el-option
          v-for="application in applications"
          :key="application.applicationId"
          :label="`${application.applicationName} · ${application.applicationCode}`"
          :value="application.applicationId"
        />
      </el-select>
      <el-select v-model="query.operationType" clearable placeholder="全部类型" aria-label="调用类型" @change="resetAndLoad">
        <el-option label="Chat" value="chat" />
        <el-option label="Embedding" value="embedding" />
        <el-option label="Rerank" value="rerank" />
      </el-select>
      <el-select v-model="query.status" clearable placeholder="全部状态" aria-label="调用状态" @change="resetAndLoad">
        <el-option label="已完成" value="completed" />
        <el-option label="失败" value="failed" />
        <el-option label="已取消" value="cancelled" />
        <el-option label="已放弃" value="abandoned" />
      </el-select>
    </section>

    <p v-if="error && rows.length" class="usage-stale" role="status">
      刷新失败，当前数据可能已过期。最近成功：{{ formatTime(lastSuccessAt) }}
    </p>
    <WorkspaceState v-if="loading && !rows.length" kind="loading" />
    <WorkspaceState v-else-if="error && !rows.length" kind="error" :message="error" @retry="loadUsage" />
    <WorkspaceState v-else-if="!selectedApplicationId" kind="empty" title="暂无可查看应用" message="创建应用后可查看调用用量。" />
    <WorkspaceState v-else-if="!rows.length" kind="empty" title="暂无用量记录" message="公开 API 调用后会在这里形成台账。" />

    <section v-else class="usage-table-wrap">
      <el-table :data="rows" size="small" style="width: 100%">
        <el-table-column prop="startedAt" label="开始时间" width="170">
          <template #default="{ row }">{{ formatTime(row.startedAt) }}</template>
        </el-table-column>
        <el-table-column prop="operationType" label="类型" width="110">
          <template #default="{ row }"><el-tag size="small" effect="plain" :type="operationMeta(row.operationType).type">{{ operationMeta(row.operationType).label }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="modelCode" label="模型" min-width="170" show-overflow-tooltip />
        <el-table-column label="Token" width="150">
          <template #default="{ row }">{{ row.inputTokens ?? '-' }} / {{ row.outputTokens ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="Search Unit" width="120">
          <template #default="{ row }">{{ row.searchUnits ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="成本" width="130">
          <template #default="{ row }">
            {{ formatCost(row.billableCost) }}
            <el-tag v-if="row.costEstimated" size="small" type="warning" effect="plain">估算</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110" />
      </el-table>
      <pagination
        v-show="total > query.pageSize"
        v-model:page="query.pageNum"
        v-model:limit="query.pageSize"
        :total="total"
        @pagination="loadUsage"
      />
    </section>
  </main>
</template>

<script setup name="AiUsage">
import { Refresh } from '@element-plus/icons-vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { listApplications, listApplicationUsage } from '../../api/application.js'
import { normalizeApplicationPage } from '../../utils/application.js'

const applications = ref([])
const selectedApplicationId = ref('')
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const lastSuccessAt = ref(null)
const query = reactive({ operationType: '', status: '', pageNum: 1, pageSize: 20 })
let requestGeneration = 0

const operationMeta = (operationType) => ({
  chat: { label: 'Chat', type: 'primary' },
  embedding: { label: 'Embedding', type: 'success' },
  rerank: { label: 'Rerank', type: 'warning' },
}[operationType] || { label: operationType || '-', type: 'info' })

async function loadApplications() {
  const page = normalizeApplicationPage(await listApplications({ pageNum: 1, pageSize: 100 }))
  applications.value = page.rows
  if (!selectedApplicationId.value && page.rows.length) {
    selectedApplicationId.value = page.rows[0].applicationId
  }
}

async function loadUsage() {
  if (!selectedApplicationId.value) return
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
  return value ? new Date(value).toLocaleString() : '-'
}

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
@media (max-width: 760px) {
  .usage-controls { grid-template-columns: 1fr; }
  .usage-table-wrap { overflow-x: auto; }
  .usage-table-wrap :deep(.el-table) { min-width: 820px; }
}
</style>
