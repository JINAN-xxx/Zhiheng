<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">最后检查：{{ formatTime(lastCheckedAt) }}</div>
      <a-tooltip title="刷新运行状态" placement="top">
        <a-button shape="circle" :loading="loading" aria-label="刷新运行状态" @click="load">
          <template #icon><ReloadOutlined /></template>
        </a-button>
      </a-tooltip>
    </div>
    <a-alert v-if="error" message="部分运行状态检查失败" type="warning" :closable="false" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-table
      class="model-table"
      :columns="columns"
      :data-source="statuses"
      :loading="loading"
      :pagination="false"
      row-key="name"
      table-layout="fixed"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">{{ record.name }}</template>
        <a-tag v-else-if="column.key === 'status'" :color="statusColors[record.type] || 'default'">{{ record.label }}</a-tag>
        <template v-else-if="column.key === 'latency'">{{ record.latency == null ? '-' : `${record.latency} ms` }}</template>
        <template v-else-if="column.key === 'checkedAt'">{{ formatTime(record.checkedAt) }}</template>
      </template>
    </a-table>
  </section>
</template>

<script setup>
import { ReloadOutlined } from '@ant-design/icons-vue'
import { getEngineRuntimeStatus } from '../../../api/modelEngine.js'
import { listPlatformModels } from '../../../api/modelCatalog.js'
import { buildRuntimeStatusRows, createRequestGate } from '../../../utils/modelEngine.js'

const loading = ref(false); const error = ref(false); const statuses = ref(buildRuntimeStatusRows()); const lastCheckedAt = ref(null)
const loadGate = createRequestGate()
const columns = [
  { title: '检查项', dataIndex: 'name', key: 'name' },
  { title: '状态', key: 'status', width: 130 },
  { title: '延迟', key: 'latency', width: 120, responsive: ['md'] },
  { title: '最后检查', key: 'checkedAt', width: 180, responsive: ['md'] },
]
const statusColors = { success: 'success', warning: 'warning', danger: 'error', info: 'default' }
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'

async function probe(request) {
  const startedAt = performance.now()
  try {
    const response = await request()
    return { ok: true, data: response?.data || {}, latencyMs: Math.round(performance.now() - startedAt), checkedAt: new Date().toISOString() }
  } catch {
    return { ok: false, data: null, latencyMs: Math.round(performance.now() - startedAt), checkedAt: new Date().toISOString() }
  }
}

async function load() {
  const requestGeneration = loadGate.begin()
  loading.value = true; error.value = false
  const [catalog, engine] = await Promise.all([
    probe(() => listPlatformModels({ pageNum: 1, pageSize: 1 })),
    probe(() => getEngineRuntimeStatus()),
  ])
  if (!loadGate.isCurrent(requestGeneration)) return
  statuses.value = buildRuntimeStatusRows({ catalog, engine })
  lastCheckedAt.value = [catalog.checkedAt, engine.checkedAt].sort().at(-1)
  error.value = !catalog.ok || !engine.ok
  loading.value = false
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-height: 360px; color: var(--color-text); }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { color: var(--color-text-secondary); font-size: 12px; }
.model-table :deep(.ant-table-container) { overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; }
.model-table :deep(.ant-table-thead > tr > th) { height: 44px; border-bottom-color: var(--gray-150); background: var(--gray-10); color: var(--color-text-secondary); }
.model-table :deep(.ant-table-tbody > tr > td) { height: 46px; border-bottom-color: var(--gray-150); background: var(--gray-0); color: var(--color-text); }
.model-table :deep(.ant-table-tbody > tr:hover > td) { background: var(--gray-50) !important; }
.model-table :deep(.ant-tag) { border: 0; border-radius: 999px; }
</style>
