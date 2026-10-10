<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary"><span>{{ total }} 个平台模型</span><span v-if="lastSuccessAt">更新于 {{ formatTime(lastSuccessAt) }}</span></div>
      <a-tooltip title="刷新平台模型" placement="top">
        <a-button shape="circle" :loading="loading" aria-label="刷新平台模型" @click="load">
          <template #icon><ReloadOutlined /></template>
        </a-button>
      </a-tooltip>
    </div>

    <a-alert v-if="error && rows.length" message="刷新失败，数据可能已过期" type="warning" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-alert v-else-if="error" message="平台模型加载失败" type="error" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-table
      v-if="rows.length || loading"
      class="model-table"
      :columns="columns"
      :data-source="rows"
      :loading="loading"
      :pagination="false"
      row-key="modelId"
      table-layout="fixed"
    >
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'modelCode'">
          <div class="mobile-model-cell">
            <div class="resource-value">
              <span class="resource-text" :title="row.modelCode">{{ row.modelCode }}</span>
              <a-tooltip title="复制模型代码" placement="top">
                <button class="copy-button" type="button" aria-label="复制模型代码" @click="copyValue(row.modelCode)"><CopyOutlined /></button>
              </a-tooltip>
            </div>
            <div v-if="props.mobile" class="mobile-model-meta">
              <a-tag :color="tagColor(modelTypeMeta(row.modelType).type)">{{ modelTypeMeta(row.modelType).label }}</a-tag>
              <a-tag :color="tagColor(binding(row).type)">{{ binding(row).label }}</a-tag>
              <a-tag :color="tagColor(lifecycle(row).type)">{{ lifecycle(row).label }}</a-tag>
            </div>
          </div>
        </template>
        <template v-else-if="column.key === 'modelType'">
          <a-tag :color="tagColor(modelTypeMeta(row.modelType).type)">{{ modelTypeMeta(row.modelType).label }}</a-tag>
        </template>
        <template v-else-if="column.key === 'bindingStatus'">
          <a-tag :color="tagColor(binding(row).type)">{{ binding(row).label }}</a-tag>
        </template>
        <template v-else-if="column.key === 'lifecycle'">
          <a-tag :color="tagColor(lifecycle(row).type)">{{ lifecycle(row).label }}</a-tag>
        </template>
        <template v-else-if="column.key === 'updatedAt'">{{ formatTime(row.updatedAt) }}</template>
        <template v-else-if="column.key === 'actions'">
          <div class="row-actions desktop-actions">
            <a-tooltip v-if="canEdit && canQuery && row.modelType === 'chat'" title="图片理解设置" placement="top"><a-button type="link" aria-label="图片理解设置" @click="openVision(row)"><template #icon><EyeOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canEdit" title="费率配置" placement="top"><a-button type="link" aria-label="费率配置" @click="openRateCards(row)"><template #icon><DollarOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canTest && row.lifecycleStatus === 'active'" title="类型化连接测试" placement="top"><a-button type="link" aria-label="连接测试" :loading="testingId === row.modelId" @click="smokeTest(row)"><template #icon><ApiOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canPublish && ['draft', 'disabled'].includes(row.lifecycleStatus)" :title="getLifecycleAction(row.lifecycleStatus)" placement="top"><a-button type="link" :aria-label="getLifecycleAction(row.lifecycleStatus)" :disabled="!canPublishPlatformModel(row)" @click="publish(row)"><template #icon><SendOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canEdit && row.lifecycleStatus === 'active'" title="停用" placement="top"><a-button type="link" class="warning-action" aria-label="停用" @click="disable(row)"><template #icon><PauseCircleOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canRemove && ['draft', 'disabled'].includes(row.lifecycleStatus)" title="删除模型" placement="top"><a-button type="link" danger aria-label="删除模型" @click="remove(row)"><template #icon><DeleteOutlined /></template></a-button></a-tooltip>
          </div>
          <a-dropdown class="mobile-actions" :trigger="['click']">
            <a-button type="text" aria-label="更多操作"><template #icon><EllipsisOutlined /></template></a-button>
            <template #overlay><a-menu @click="({ key }) => handleRowAction(key, row)">
              <a-menu-item v-if="canEdit && canQuery && row.modelType === 'chat'" key="vision">图片理解设置</a-menu-item>
              <a-menu-item v-if="canEdit" key="rate">费率配置</a-menu-item>
              <a-menu-item v-if="canTest && row.lifecycleStatus === 'active'" key="test">类型化连接测试</a-menu-item>
              <a-menu-item v-if="canPublish && ['draft', 'disabled'].includes(row.lifecycleStatus)" key="publish" :disabled="!canPublishPlatformModel(row)">{{ getLifecycleAction(row.lifecycleStatus) }}</a-menu-item>
              <a-menu-item v-if="canEdit && row.lifecycleStatus === 'active'" key="disable">停用</a-menu-item>
              <a-menu-item v-if="canRemove && ['draft', 'disabled'].includes(row.lifecycleStatus)" key="remove" danger>删除模型</a-menu-item>
            </a-menu></template>
          </a-dropdown>
        </template>
      </template>
    </a-table>
    <WorkspaceState v-else-if="!error" kind="empty" title="暂无平台模型" />
    <a-pagination
      v-if="total > pageSize"
      v-model:current="pageNum"
      :page-size="pageSize"
      :total="total"
      :show-size-changer="false"
      size="small"
      @change="load"
    />

    <ModelVisionDialog v-model="visionDialogVisible" :model="visionModel" @saved="load" />
    <a-modal v-model:open="rateDialogVisible" title="费率配置" width="min(680px, 94vw)" destroy-on-close>
      <div class="rate-context">
        <strong>{{ rateModel?.modelCode }}</strong>
        <a-tag :color="tagColor(modelTypeMeta(rateModel?.modelType).type)">
          {{ modelTypeMeta(rateModel?.modelType).label }}
        </a-tag>
        <span class="muted">{{ rateForm.billingMode === 'search_unit' ? '按检索单元计费' : '按令牌计费' }}</span>
      </div>
      <a-table :columns="rateColumns" :data-source="rateCards" :loading="rateLoading" :pagination="false" :scroll="{ y: 220 }" row-key="version" size="small" table-layout="fixed">
        <template #bodyCell="{ column, record: row }">
          <template v-if="column.key === 'price'">
            <span v-if="row.billingMode === 'search_unit'">{{ row.searchPricePerUnit }} / 单元</span>
            <span v-else>输入 {{ row.inputPricePerToken }} / 输出 {{ row.outputPricePerToken }}</span>
          </template>
        </template>
      </a-table>
      <a-divider orientation="left">新增价格版本</a-divider>
      <a-form layout="vertical" @submit.prevent>
        <template v-if="rateForm.billingMode === 'token'">
          <div class="rate-grid">
            <a-form-item label="输入令牌单价"><a-input v-model:value="rateForm.inputPricePerToken" /></a-form-item>
            <a-form-item label="输出令牌单价"><a-input v-model:value="rateForm.outputPricePerToken" /></a-form-item>
            <a-form-item label="缓存令牌单价（可选）"><a-input v-model:value="rateForm.cachePricePerToken" /></a-form-item>
          </div>
        </template>
        <a-form-item v-else label="检索单元单价"><a-input v-model:value="rateForm.searchPricePerUnit" /></a-form-item>
        <a-form-item label="币种"><a-input v-model:value="rateForm.currency" :maxlength="3" /></a-form-item>
      </a-form>
      <template #footer>
        <a-button @click="rateDialogVisible = false">关闭</a-button>
        <a-button type="primary" :loading="rateSaving" @click="saveRateCard">创建价格版本</a-button>
      </template>
    </a-modal>
  </section>
</template>

<script setup>
import {
  ApiOutlined, CopyOutlined, DeleteOutlined, DollarOutlined, EllipsisOutlined, EyeOutlined,
  PauseCircleOutlined, ReloadOutlined, SendOutlined,
} from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import ModelVisionDialog from './ModelVisionDialog.vue'
import {
  deletePlatformModel,
  disablePlatformModel,
  getPlatformModel,
  listModelRateCards,
  listPlatformModels,
  publishPlatformModel,
  createModelRateCard,
  smokeTestPlatformModel,
} from '../../../api/modelCatalog.js'
import {
  canPublishPlatformModel, createRequestGate, getBindingStatusMeta, getLastAvailablePage, getLifecycleAction, getLifecycleStatusMeta,
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
const canTest = computed(() => can('ai:model:test'))
const hasAnyAction = computed(() => canPublish.value || canEdit.value || canRemove.value || canTest.value)
const columns = computed(() => [
  { title: '模型代码', key: 'modelCode', width: props.mobile ? 180 : 160, align: 'left' },
  !props.mobile && { title: '类型', key: 'modelType', width: 110, align: 'center' },
  !props.mobile && { title: '绑定状态', key: 'bindingStatus', width: 100, align: 'center' },
  !props.mobile && { title: '发布状态', key: 'lifecycle', width: 90, align: 'center' },
  !props.mobile && { title: '更新时间', key: 'updatedAt', width: 140, align: 'left' },
  hasAnyAction.value && { title: '操作', key: 'actions', width: props.mobile ? 64 : 180, align: 'right' },
].filter(Boolean))
const rateColumns = [
  { title: '版本', dataIndex: 'version', key: 'version', width: 72, align: 'center' },
  { title: '模式', dataIndex: 'billingMode', key: 'billingMode', width: 112, align: 'center' },
  { title: '价格', key: 'price', align: 'left' },
  { title: '币种', dataIndex: 'currency', key: 'currency', width: 72, align: 'center' },
  { title: '状态', dataIndex: 'status', key: 'status', width: 78, align: 'center' },
]
const testingId = ref(null)
const visionDialogVisible = ref(false)
const visionModel = ref(null)
function openVision(row) {
  visionModel.value = row
  visionDialogVisible.value = true
}
const rateDialogVisible = ref(false)
const rateLoading = ref(false)
const rateSaving = ref(false)
const rateCards = ref([])
const rateModel = ref(null)
const rateForm = reactive({
  billingMode: 'token',
  inputPricePerToken: '0',
  outputPricePerToken: '0',
  cachePricePerToken: '',
  searchPricePerUnit: '0.001',
  currency: 'USD',
})

function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  const pad = (number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

const lifecycle = (row) => getLifecycleStatusMeta(row.lifecycleStatus)
const binding = (row) => getBindingStatusMeta(row.bindingStatus)

const modelTypeMeta = (modelType) => ({
  chat: { label: '对话', type: 'primary' },
  embedding: { label: '向量', type: 'success' },
  rerank: { label: '排序', type: 'warning' },
}[modelType] || { label: modelType || '-', type: 'info' })
const tagColor = (type) => ({ primary: 'blue', success: 'green', warning: 'orange', danger: 'red' }[type])

const billingModeFor = (modelType) => modelType === 'rerank' ? 'search_unit' : 'token'

async function loadRateCards() {
  if (!rateModel.value) return
  rateLoading.value = true
  try {
    const response = await listModelRateCards(rateModel.value.modelId)
    rateCards.value = Array.isArray(response?.data) ? response.data : []
  } catch {
    feedback.msgError('费率加载失败')
  } finally {
    rateLoading.value = false
  }
}

async function openRateCards(row) {
  rateModel.value = row
  rateForm.billingMode = billingModeFor(row.modelType)
  rateForm.inputPricePerToken = '0'
  rateForm.outputPricePerToken = '0'
  rateForm.cachePricePerToken = ''
  rateForm.searchPricePerUnit = '0.001'
  rateForm.currency = 'USD'
  rateCards.value = []
  rateDialogVisible.value = true
  await loadRateCards()
}

async function saveRateCard() {
  if (!rateModel.value) return
  const payload = {
    billingMode: rateForm.billingMode,
    inputPricePerToken: rateForm.billingMode === 'token' ? rateForm.inputPricePerToken : '0',
    outputPricePerToken: rateForm.billingMode === 'token' ? rateForm.outputPricePerToken : '0',
    cachePricePerToken: rateForm.billingMode === 'token' && rateForm.cachePricePerToken
      ? rateForm.cachePricePerToken
      : null,
    searchPricePerUnit: rateForm.billingMode === 'search_unit' ? rateForm.searchPricePerUnit : null,
    currency: rateForm.currency.toUpperCase(),
  }
  rateSaving.value = true
  try {
    await createModelRateCard(rateModel.value.modelId, payload)
    feedback.msgSuccess('价格版本已创建')
    await loadRateCards()
  } catch {
    feedback.msgError('价格版本创建失败，请检查单价和币种')
  } finally {
    rateSaving.value = false
  }
}

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
  feedback.msgSuccess('已复制')
}

async function publish(row) {
  if (!canPublishPlatformModel(row)) {
    feedback.msgWarning('仅可发布已激活运行时绑定的模型')
    return
  }
  const isReenable = row.lifecycleStatus === 'disabled'
  try {
    await publishPlatformModel(row.modelId)
    feedback.msgSuccess(isReenable ? '模型已重新启用' : '模型已发布')
    await load()
  } catch { feedback.msgError(isReenable ? '重新启用失败' : '发布失败') }
}

async function disable(row) {
  try {
    await feedback.confirm(`确认停用 ${row.modelCode}？`)
    await disablePlatformModel(row.modelId)
    feedback.msgSuccess('模型已停用')
    await load()
  } catch (event) {
    if (event !== 'cancel' && event !== 'close') feedback.msgError('停用失败')
  }
}

async function remove(row) {
  try {
    await feedback.confirm(`确认删除模型 ${row.modelCode}？删除后不可恢复。`)
    await deletePlatformModel(row.modelId)
    feedback.msgSuccess('模型已删除')
    await load()
  } catch (event) {
    if (event !== 'cancel' && event !== 'close') feedback.msgError(event?.message || '删除失败')
  }
}

async function smokeTest(row) {
  testingId.value = row.modelId
  try {
    const response = await smokeTestPlatformModel(row.modelId)
    const result = response?.data
    if (result?.success) {
      feedback.msgSuccess(`${modelTypeMeta(result.modelType).label} 连接测试成功（${result.latencyMs ?? '-'} ms）`)
    } else {
      feedback.msgError(`${modelTypeMeta(result.modelType).label} 连接测试失败：${result?.message || '未知错误'}`)
    }
  } catch {
    feedback.msgError('连接测试请求失败')
  } finally {
    testingId.value = null
  }
}

function handleRowAction(action, row) {
  const actions = {
    vision: openVision,
    rate: openRateCards,
    test: smokeTest,
    publish,
    disable,
    remove,
  }
  actions[action]?.(row)
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-width: 0; min-height: 360px; color: var(--color-text); }
.panel-toolbar { display: flex; min-height: 54px; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 20px; padding: 8px 12px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--bg-container); }
.panel-summary { display: flex; min-width: 0; flex-wrap: wrap; align-items: center; gap: 4px 12px; color: var(--color-text-secondary); font-size: 13px; }
.panel-toolbar > :deep(.ant-btn) { flex-shrink: 0; }
.workspace-panel > :deep(.ant-alert) { margin-bottom: 14px; }
.model-table :deep(.ant-table) { font-family: var(--workspace-font-family); font-size: 14px; }
.model-table :deep(.ant-table-container) { overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; }
.model-table :deep(.ant-table-thead > tr > th) { border-bottom-color: var(--gray-150); background: var(--gray-10); color: var(--color-text-secondary); font-size: 13px; font-weight: 500; }
.model-table :deep(.ant-table-tbody > tr > td) { border-bottom-color: var(--gray-150); background: var(--bg-container); color: var(--color-text); }
.model-table :deep(.ant-table-cell) { vertical-align: middle; }
.model-table :deep(.ant-table-tbody > tr:hover > td) { background: var(--gray-50) !important; }
.model-table :deep(.ant-tag) { margin: 0; }
.row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 5px; }
.resource-value { display: flex; min-width: 0; align-items: center; gap: 5px; }
.mobile-model-cell { display: grid; min-width: 0; gap: 8px; }
.mobile-model-meta { display: flex; flex-wrap: wrap; gap: 4px; }
.resource-text { min-width: 0; overflow: hidden; font-family: var(--workspace-font-mono); font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.copy-button { display: inline-flex; width: 24px; height: 24px; flex: 0 0 24px; align-items: center; justify-content: center; border: 0; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.copy-button:hover { background: var(--gray-10); color: var(--main-700); }
.copy-button:focus-visible { outline: 2px solid var(--main-color); outline-offset: 2px; }
.copy-button svg { width: 14px; height: 14px; }
.muted { color: var(--color-text-tertiary); }
.warning-action { color: var(--color-warning-700); }
.rate-context { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.rate-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.mobile-actions { display: none; }
:deep(.ant-pagination) { display: flex; justify-content: flex-end; margin-top: 18px; }
@media (max-width: 760px) {
  .desktop-actions { display: none; }
  .mobile-actions { display: inline-flex; }
  :deep(.ant-pagination) { justify-content: center; }
  .rate-grid { grid-template-columns: 1fr; gap: 0; }
}
</style>
