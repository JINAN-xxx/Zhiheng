<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">{{ total }} 个平台模型<span v-if="lastSuccessAt"> · 更新于 {{ formatTime(lastSuccessAt) }}</span></div>
      <el-tooltip content="刷新平台模型" placement="top">
        <el-button circle :icon="Refresh" :loading="loading" aria-label="刷新平台模型" @click="load" />
      </el-tooltip>
    </div>

    <el-alert v-if="error && rows.length" title="刷新失败，数据可能已过期" type="warning" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-alert v-else-if="error" title="平台模型加载失败" type="error" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-table v-if="rows.length || loading" v-loading="loading" :data="rows" table-layout="fixed" class="model-table">
      <el-table-column v-if="!mobile" label="Model ID" width="180">
        <template #default="{ row }">
          <div class="resource-value">
            <span class="resource-text" :title="row.modelId">{{ row.modelId }}</span>
            <el-tooltip content="复制 Model ID" placement="top">
              <button class="copy-button" type="button" aria-label="复制 Model ID" @click="copyValue(row.modelId)"><el-icon><DocumentCopy /></el-icon></button>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="Model Code" :width="mobile ? undefined : 160" :min-width="mobile ? 140 : undefined">
        <template #default="{ row }">
          <div class="resource-value">
            <span class="resource-text" :title="row.modelCode">{{ row.modelCode }}</span>
            <el-tooltip content="复制 Model Code" placement="top">
              <button class="copy-button" type="button" aria-label="复制 Model Code" @click="copyValue(row.modelCode)"><el-icon><DocumentCopy /></el-icon></button>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="!mobile" prop="modelType" label="类型" width="100">
        <template #default="{ row }">{{ row.modelType === 'embedding' ? 'Embedding' : 'Chat' }}</template>
      </el-table-column>
      <el-table-column v-if="!mobile" label="Capability" width="194">
        <template #default="{ row }">
          <div class="tag-list">
            <el-tag v-for="item in row.capabilities" :key="item.key" :type="item.type" size="small" effect="plain">{{ item.label }}</el-tag>
            <span v-if="row.capabilities.length === 0" class="muted">-</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="!mobile" label="Binding Alias" width="160">
        <template #default="{ row }">
          <div class="binding-value">
            <div v-if="row.bindingAlias" class="resource-value">
              <span class="resource-text" :title="row.bindingAlias">{{ row.bindingAlias }}</span>
              <el-tooltip content="复制 Binding Alias" placement="top">
                <button class="copy-button" type="button" aria-label="复制 Binding Alias" @click="copyValue(row.bindingAlias)"><el-icon><DocumentCopy /></el-icon></button>
              </el-tooltip>
            </div>
            <span v-else class="muted">-</span>
            <el-tag :type="binding(row).type" size="small" effect="plain">{{ binding(row).label }}</el-tag>
          </div>
        </template>
      </el-table-column>
      <el-table-column v-if="!mobile" label="生命周期" width="90">
        <template #default="{ row }"><el-tag :type="lifecycle(row).type" size="small" effect="light">{{ lifecycle(row).label }}</el-tag></template>
      </el-table-column>
      <el-table-column v-if="!mobile" label="更新时间" width="140">
        <template #default="{ row }">{{ formatTime(row.updatedAt) }}</template>
      </el-table-column>
      <el-table-column v-if="hasAnyAction" label="操作" :width="mobile ? 86 : 104" align="right">
        <template #default="{ row }">
          <div class="row-actions desktop-actions">
            <el-tooltip v-if="canPublish && row.lifecycleStatus === 'draft'" content="发布" placement="top">
              <el-button link type="primary" :icon="Promotion" aria-label="发布" :disabled="!canPublishPlatformModel(row)" @click="publish(row)" />
            </el-tooltip>
            <el-tooltip v-if="canEdit && row.lifecycleStatus === 'active'" content="停用" placement="top">
              <el-button link type="warning" :icon="VideoPause" aria-label="停用" @click="disable(row)" />
            </el-tooltip>
            <el-tooltip v-if="canRemove && row.lifecycleStatus === 'draft'" content="删除草稿" placement="top">
              <el-button link type="danger" :icon="Delete" aria-label="删除草稿" @click="remove(row)" />
            </el-tooltip>
          </div>
          <el-dropdown class="mobile-actions" trigger="click">
            <el-button link :icon="MoreFilled" aria-label="更多操作">更多</el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item v-if="canPublish && row.lifecycleStatus === 'draft'" :disabled="!canPublishPlatformModel(row)" @click="publish(row)">发布</el-dropdown-item>
              <el-dropdown-item v-if="canEdit && row.lifecycleStatus === 'active'" @click="disable(row)">停用</el-dropdown-item>
              <el-dropdown-item v-if="canRemove && row.lifecycleStatus === 'draft'" @click="remove(row)">删除草稿</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else-if="!error" description="暂无平台模型" :image-size="72" />
    <el-pagination
      v-if="total > pageSize"
      v-model:current-page="pageNum"
      :page-size="pageSize"
      :total="total"
      layout="prev, pager, next"
      small
      @current-change="load"
    />
  </section>
</template>

<script setup>
import { Delete, DocumentCopy, MoreFilled, Promotion, Refresh, VideoPause } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deletePlatformModel,
  disablePlatformModel,
  getPlatformModel,
  listPlatformModels,
  publishPlatformModel,
} from '../../../api/modelCatalog.js'
import {
  canPublishPlatformModel, createRequestGate, getBindingStatusMeta, getLastAvailablePage, getLifecycleStatusMeta,
  hasPermission, normalizePlatformModel,
} from '../../../utils/modelEngine.js'

const props = defineProps({
  permissions: { type: Array, default: () => [] },
  mobile: { type: Boolean, default: false },
})

const rows = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref(null)
const lastSuccessAt = ref(null)
const pageNum = ref(1)
const pageSize = 10
const loadGate = createRequestGate()
const can = (permission) => hasPermission(props.permissions, permission)
const canQuery = computed(() => can('ai:model:query'))
const canPublish = computed(() => can('ai:model:publish'))
const canEdit = computed(() => can('ai:model:edit'))
const canRemove = computed(() => can('ai:model:remove'))
const hasAnyAction = computed(() => canPublish.value || canEdit.value || canRemove.value)

function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const lifecycle = (row) => getLifecycleStatusMeta(row.lifecycleStatus)
const binding = (row) => getBindingStatusMeta(row.bindingStatus)

async function load() {
  const requestGeneration = loadGate.begin()
  loading.value = true
  error.value = null
  try {
    const response = await listPlatformModels({ pageNum: pageNum.value, pageSize })
    if (!loadGate.isCurrent(requestGeneration)) return
    const sourceRows = Array.isArray(response?.rows) ? response.rows : []
    const responseTotal = Number(response?.total)
    const nextTotal = Number.isFinite(responseTotal) && responseTotal >= 0 ? responseTotal : sourceRows.length
    const availablePage = getLastAvailablePage({
      pageNum: pageNum.value,
      pageSize,
      total: nextTotal,
      rowsLength: sourceRows.length,
    })
    if (availablePage !== pageNum.value) {
      pageNum.value = availablePage
      return load()
    }
    let detailRows = sourceRows.map(normalizePlatformModel)
    if (canQuery.value) {
      const detailResults = await Promise.allSettled(sourceRows.map((item) => getPlatformModel(item.modelId)))
      detailRows = detailResults.map((result, index) => (
        result.status === 'fulfilled' ? normalizePlatformModel(result.value?.data || sourceRows[index]) : normalizePlatformModel(sourceRows[index])
      ))
    }
    if (!loadGate.isCurrent(requestGeneration)) return
    rows.value = detailRows
    total.value = nextTotal
    lastSuccessAt.value = new Date().toISOString()
  } catch (event) {
    if (!loadGate.isCurrent(requestGeneration)) return
    if (rows.value.length === 0) total.value = 0
    error.value = event
  } finally {
    if (loadGate.isCurrent(requestGeneration)) loading.value = false
  }
}

async function copyValue(value) {
  if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value)
  else {
    const input = document.createElement('textarea')
    input.value = value
    input.style.position = 'fixed'
    input.style.opacity = '0'
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    input.remove()
  }
  ElMessage.success('已复制')
}

async function publish(row) {
  if (!canPublishPlatformModel(row)) {
    ElMessage.warning('仅可发布已激活运行时绑定的模型')
    return
  }
  try {
    await publishPlatformModel(row.modelId)
    ElMessage.success('模型已发布')
    await load()
  } catch { ElMessage.error('发布失败') }
}

async function disable(row) {
  try {
    await ElMessageBox.confirm(`确认停用 ${row.modelCode}？`, '停用模型', { type: 'warning' })
    await disablePlatformModel(row.modelId)
    ElMessage.success('模型已停用')
    await load()
  } catch (event) {
    if (event !== 'cancel' && event !== 'close') ElMessage.error('停用失败')
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确认删除草稿 ${row.modelCode}？`, '删除草稿', { type: 'warning' })
    await deletePlatformModel(row.modelId)
    ElMessage.success('草稿已删除')
    await load()
  } catch (event) {
    if (event !== 'cancel' && event !== 'close') ElMessage.error('删除失败')
  }
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-height: 360px; }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { color: var(--el-text-color-secondary); font-size: 12px; }
.tag-list, .row-actions { display: flex; align-items: center; gap: 6px; }
.binding-value { display: flex; min-width: 0; align-items: center; gap: 6px; }
.binding-value .resource-value { flex: 1 1 auto; }
.row-actions { justify-content: flex-end; }
.resource-value { display: flex; min-width: 0; align-items: center; gap: 5px; }
.resource-text { min-width: 0; overflow: hidden; font-family: var(--ai-font-mono); text-overflow: ellipsis; white-space: nowrap; }
.copy-button { display: inline-flex; width: 24px; height: 24px; flex: 0 0 24px; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--el-text-color-secondary); cursor: pointer; }
.copy-button:hover { color: var(--el-color-primary); }
.copy-button svg { width: 14px; height: 14px; }
.muted { color: var(--el-text-color-placeholder); }
.mobile-actions { display: none; }
:deep(.el-pagination) { justify-content: flex-end; margin-top: 14px; }
@media (max-width: 760px) {
  :deep(.mobile-hidden), .desktop-actions { display: none; }
  .mobile-actions { display: inline-flex; }
  :deep(.el-pagination) { justify-content: center; }
}
</style>
