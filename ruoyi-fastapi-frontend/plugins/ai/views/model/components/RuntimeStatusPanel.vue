<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">最后检查：{{ formatTime(lastCheckedAt) }}</div>
      <el-tooltip content="刷新运行状态" placement="top"><el-button circle :icon="Refresh" :loading="loading" aria-label="刷新运行状态" @click="load" /></el-tooltip>
    </div>
    <el-alert v-if="error" title="部分运行状态检查失败" type="warning" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-table v-loading="loading" :data="statuses" table-layout="fixed">
      <el-table-column prop="name" label="检查项" min-width="190" />
      <el-table-column label="状态" width="130"><template #default="{ row }"><el-tag :type="row.type" size="small" effect="light">{{ row.label }}</el-tag></template></el-table-column>
      <el-table-column label="延迟" width="120" class-name="mobile-hidden"><template #default="{ row }">{{ row.latency == null ? '-' : `${row.latency} ms` }}</template></el-table-column>
      <el-table-column label="最后检查" min-width="180" class-name="mobile-hidden"><template #default="{ row }">{{ formatTime(row.checkedAt) }}</template></el-table-column>
    </el-table>
  </section>
</template>

<script setup>
import { Refresh } from '@element-plus/icons-vue'
import { getEngineRuntimeStatus } from '../../../api/modelEngine.js'
import { listPlatformModels } from '../../../api/modelCatalog.js'
import { buildRuntimeStatusRows, createRequestGate } from '../../../utils/modelEngine.js'

const loading = ref(false); const error = ref(false); const statuses = ref(buildRuntimeStatusRows()); const lastCheckedAt = ref(null)
const loadGate = createRequestGate()
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
.workspace-panel { min-height: 360px; }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { color: var(--el-text-color-secondary); font-size: 12px; }
@media (max-width: 760px) { :deep(.mobile-hidden) { display: none; } }
</style>
