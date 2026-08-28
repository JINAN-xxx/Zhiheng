<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">{{ rows.length }} 个引擎部署<span v-if="lastSuccessAt"> · 更新于 {{ formatTime(lastSuccessAt) }}</span></div>
      <a-tooltip title="刷新引擎部署" placement="top"><a-button shape="circle" :loading="loading" aria-label="刷新引擎部署" @click="load"><template #icon><ReloadOutlined /></template></a-button></a-tooltip>
    </div>
    <a-alert v-if="notConfigured" :message="rows.length ? '管理密钥未配置，当前数据可能已过期' : '管理密钥未配置'" type="warning" show-icon />
    <a-alert v-else-if="error && rows.length" message="刷新失败，数据可能已过期" type="warning" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-alert v-else-if="error" message="引擎部署加载失败" type="error" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-table v-if="rows.length || loading" class="model-table" :columns="columns" :data-source="rows" :loading="loading" :pagination="false" row-key="deploymentId" table-layout="fixed">
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'blocked'"><a-tag :color="row.blocked ? 'red' : 'green'">{{ row.blocked ? '是' : '否' }}</a-tag></template>
        <template v-else-if="column.key === 'updatedAt'">{{ formatTime(row.updatedAt) }}</template>
        <template v-else-if="column.key === 'actions'">
          <div class="row-actions desktop-actions">
            <a-tooltip v-if="canEdit" title="编辑" placement="top"><a-button type="link" aria-label="编辑" @click="openEdit(row)"><template #icon><EditOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canTest" title="测试连接" placement="top"><a-button type="link" aria-label="测试连接" @click="test(row)"><template #icon><ApiOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canRemove" title="删除" placement="top"><a-button type="link" danger aria-label="删除" @click="remove(row)"><template #icon><DeleteOutlined /></template></a-button></a-tooltip>
          </div>
          <a-dropdown class="mobile-actions" :trigger="['click']">
            <a-button type="text" aria-label="更多操作"><template #icon><EllipsisOutlined /></template></a-button>
            <template #overlay><a-menu @click="({ key }) => handleRowAction(key, row)">
              <a-menu-item v-if="canEdit" key="edit">编辑</a-menu-item>
              <a-menu-item v-if="canTest" key="test">测试连接</a-menu-item>
              <a-menu-item v-if="canRemove" key="remove" danger>删除</a-menu-item>
            </a-menu></template>
          </a-dropdown>
        </template>
      </template>
    </a-table>
    <a-empty v-else-if="!error && !notConfigured" description="暂无引擎部署" :image-style="{ height: '72px' }" />

    <a-drawer v-model:open="drawerOpen" root-class-name="model-form-drawer" title="编辑引擎部署" width="min(720px, 100%)" destroy-on-close>
      <DeploymentForm
        v-if="editing"
        :deployment="editing"
        :providers="providers"
        :credentials="credentials"
        mode="edit"
        :submitting="saving"
        :test-loading="testing"
        @submit="save"
        @test="confirmTest"
        @cancel="drawerOpen = false"
      />
    </a-drawer>
  </section>
</template>

<script setup>
import { ApiOutlined, DeleteOutlined, EditOutlined, EllipsisOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import {
  deleteEngineDeployment, listEngineCredentials, listEngineDeployments,
  listEngineProviders, testEngineConnection, updateEngineDeployment,
} from '../../../api/modelEngine.js'
import {
  createRequestGate, hasPermission, isManagementMisconfiguredError, normalizeCredential, normalizeDeployment, normalizeProvider,
} from '../../../utils/modelEngine.js'
import DeploymentForm from './DeploymentForm.vue'

const props = defineProps({
  permissions: { type: Array, default: () => [] },
  mobile: { type: Boolean, default: false },
})

const rows = ref([])
const providers = ref([])
const credentials = ref([])
const loading = ref(false)
const error = ref(false)
const notConfigured = ref(false)
const drawerOpen = ref(false)
const editing = ref(null)
const saving = ref(false)
const testing = ref(false)
const lastSuccessAt = ref(null)
const optionsAvailable = ref(false)
const loadGate = createRequestGate()
const can = (permission) => hasPermission(props.permissions, permission)
const canEdit = computed(() => can('ai:engine:deployment:edit') && can('ai:engine:credential:list'))
const canTest = computed(() => can('ai:model:test'))
const canRemove = computed(() => can('ai:engine:deployment:remove'))
const hasAnyAction = computed(() => !notConfigured.value && (canEdit.value || canTest.value || canRemove.value))
const columns = computed(() => [
  { title: '模型', dataIndex: 'baseModel', key: 'baseModel', width: props.mobile ? 180 : 170, ellipsis: true },
  !props.mobile && { title: '服务商', dataIndex: 'provider', key: 'provider', width: 130 },
  !props.mobile && { title: '已阻止', key: 'blocked', width: 90 },
  !props.mobile && { title: '更新时间', key: 'updatedAt', width: 168 },
  hasAnyAction.value && { title: '操作', key: 'actions', width: props.mobile ? 64 : 132, align: 'right' },
].filter(Boolean))

const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'

async function load() {
  const requestGeneration = loadGate.begin()
  loading.value = true; error.value = false
  try {
    const deploymentResponse = await listEngineDeployments()
    if (!loadGate.isCurrent(requestGeneration)) return
    notConfigured.value = false
    rows.value = (deploymentResponse?.data || []).map(normalizeDeployment)
    lastSuccessAt.value = new Date().toISOString()
    if (can('ai:engine:credential:list')) {
      const [providerResult, credentialResult] = await Promise.allSettled([listEngineProviders(), listEngineCredentials()])
      if (!loadGate.isCurrent(requestGeneration)) return
      optionsAvailable.value = providerResult.status === 'fulfilled' && credentialResult.status === 'fulfilled'
      if (providerResult.status === 'fulfilled') providers.value = (providerResult.value?.data || []).map(normalizeProvider).filter(Boolean)
      if (credentialResult.status === 'fulfilled') credentials.value = (credentialResult.value?.data || []).map(normalizeCredential)
    }
  } catch (event) {
    if (!loadGate.isCurrent(requestGeneration)) return
    const managementMisconfigured = isManagementMisconfiguredError(event)
    if (managementMisconfigured) notConfigured.value = true
    error.value = !managementMisconfigured
    if (managementMisconfigured) { drawerOpen.value = false; editing.value = null }
  }
  finally { if (loadGate.isCurrent(requestGeneration)) loading.value = false }
}

function openEdit(row) {
  if (notConfigured.value) return
  if (!optionsAvailable.value) { feedback.msgError('编辑选项加载失败，请刷新后重试'); return }
  editing.value = row; drawerOpen.value = true
}

async function runTest(payload) {
  if (notConfigured.value) return
  await feedback.confirm('连接测试可能触发服务商请求并产生极小费用，确认继续？')
  testing.value = true
  try {
    const response = await testEngineConnection(payload)
    if (response?.data?.success) feedback.msgSuccess(`连接成功${response.data.latencyMs != null ? `，${response.data.latencyMs} ms` : ''}`)
    else feedback.msgError('连接测试未通过')
  } finally { testing.value = false }
}

async function test(row) {
  if (notConfigured.value) return
  try { await runTest({ upstreamModel: row.baseModel, provider: row.provider, credentialName: row.credentialName, values: {} }) }
  catch (event) { if (event !== 'cancel' && event !== 'close') feedback.msgError('连接测试失败') }
}
async function confirmTest(payload) {
  if (notConfigured.value) return
  try { await runTest(payload) } catch (event) { if (event !== 'cancel' && event !== 'close') feedback.msgError('连接测试失败') }
}

async function save(payload) {
  if (notConfigured.value) return
  saving.value = true
  try { await updateEngineDeployment(editing.value.deploymentId, payload); feedback.msgSuccess('部署已更新'); drawerOpen.value = false; await load() }
  catch { feedback.msgError('部署更新失败') } finally { saving.value = false }
}

async function remove(row) {
  if (notConfigured.value) return
  try {
    await feedback.confirm(`确认删除部署 ${row.alias}？`)
    await deleteEngineDeployment(row.deploymentId); feedback.msgSuccess('部署已删除'); await load()
  } catch (event) { if (event !== 'cancel' && event !== 'close') feedback.msgError('部署删除失败') }
}

function handleRowAction(action, row) {
  const actions = { edit: openEdit, test, remove }
  actions[action]?.(row)
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-height: 360px; color: var(--color-text); }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { color: var(--color-text-secondary); font-size: 13px; }
.model-table :deep(.ant-table-container) { overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; }
.model-table :deep(.ant-table-thead > tr > th) { border-bottom-color: var(--gray-150); background: var(--gray-10); color: var(--color-text-secondary); }
.model-table :deep(.ant-table-tbody > tr > td) { border-bottom-color: var(--gray-150); background: var(--bg-container); color: var(--color-text); }
.model-table :deep(.ant-table-tbody > tr:hover > td) { background: var(--gray-50) !important; }
.model-table :deep(.ant-tag) { border: 0; border-radius: 999px; }
.row-actions { display: flex; justify-content: flex-end; gap: 5px; }
.mobile-actions { display: none; }
code { font-family: var(--ai-font-mono); color: var(--color-text); font-size: 13px; }
:global(.model-form-drawer .ant-drawer-content) { background: var(--bg-elevated); }
:global(.model-form-drawer .ant-drawer-header) { border-bottom: 1px solid var(--gray-150); background: var(--bg-elevated); }
@media (max-width: 760px) {
  .desktop-actions { display: none; }
  .mobile-actions { display: inline-flex; }
}
</style>
