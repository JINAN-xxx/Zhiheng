<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">{{ rows.length }} 个引擎部署<span v-if="lastSuccessAt"> · 更新于 {{ formatTime(lastSuccessAt) }}</span></div>
      <el-tooltip content="刷新引擎部署" placement="top"><el-button circle :icon="Refresh" :loading="loading" aria-label="刷新引擎部署" @click="load" /></el-tooltip>
    </div>
    <el-alert v-if="notConfigured" :title="rows.length ? 'Management Key 未配置，当前数据可能已过期' : 'Management Key 未配置'" type="warning" :closable="false" show-icon />
    <el-alert v-else-if="error && rows.length" title="刷新失败，数据可能已过期" type="warning" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-alert v-else-if="error" title="引擎部署加载失败" type="error" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-table v-if="rows.length || loading" v-loading="loading" :data="rows" table-layout="fixed">
      <el-table-column v-if="!mobile" prop="deploymentId" label="Deployment ID" min-width="190" show-overflow-tooltip><template #default="{ row }"><code>{{ row.deploymentId }}</code></template></el-table-column>
      <el-table-column prop="alias" label="Alias" min-width="150" show-overflow-tooltip><template #default="{ row }"><code>{{ row.alias }}</code></template></el-table-column>
      <el-table-column v-if="!mobile" prop="provider" label="Provider" width="130" />
      <el-table-column v-if="!mobile" prop="baseModel" label="Base Model" min-width="170" show-overflow-tooltip />
      <el-table-column v-if="!mobile" label="Blocked" width="90"><template #default="{ row }"><el-tag :type="row.blocked ? 'danger' : 'success'" size="small" effect="plain">{{ row.blocked ? '是' : '否' }}</el-tag></template></el-table-column>
      <el-table-column v-if="!mobile" label="更新时间" width="168"><template #default="{ row }">{{ formatTime(row.updatedAt) }}</template></el-table-column>
      <el-table-column v-if="hasAnyAction" label="操作" :width="mobile ? 86 : 112" align="right">
        <template #default="{ row }">
          <div class="row-actions desktop-actions">
            <el-tooltip v-if="canEdit" content="编辑" placement="top"><el-button link type="primary" :icon="Edit" aria-label="编辑" @click="openEdit(row)" /></el-tooltip>
            <el-tooltip v-if="canTest" content="测试连接" placement="top"><el-button link type="primary" :icon="Connection" aria-label="测试连接" @click="test(row)" /></el-tooltip>
            <el-tooltip v-if="canRemove" content="删除" placement="top"><el-button link type="danger" :icon="Delete" aria-label="删除" @click="remove(row)" /></el-tooltip>
          </div>
          <el-dropdown class="mobile-actions" trigger="click">
            <el-button link :icon="MoreFilled" aria-label="更多操作">更多</el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item v-if="canEdit" @click="openEdit(row)">编辑</el-dropdown-item>
              <el-dropdown-item v-if="canTest" @click="test(row)">测试连接</el-dropdown-item>
              <el-dropdown-item v-if="canRemove" @click="remove(row)">删除</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else-if="!error && !notConfigured" description="暂无引擎部署" :image-size="72" />

    <el-drawer v-model="drawerOpen" title="编辑引擎部署" size="min(720px, 100%)" destroy-on-close>
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
    </el-drawer>
  </section>
</template>

<script setup>
import { Connection, Delete, Edit, MoreFilled, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
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
  if (!optionsAvailable.value) { ElMessage.error('编辑选项加载失败，请刷新后重试'); return }
  editing.value = row; drawerOpen.value = true
}

async function runTest(payload) {
  if (notConfigured.value) return
  await ElMessageBox.confirm('连接测试可能触发 Provider 请求并产生极小费用，确认继续？', '测试连接', { type: 'warning' })
  testing.value = true
  try {
    const response = await testEngineConnection(payload)
    if (response?.data?.success) ElMessage.success(`连接成功${response.data.latencyMs != null ? `，${response.data.latencyMs} ms` : ''}`)
    else ElMessage.error('连接测试未通过')
  } finally { testing.value = false }
}

async function test(row) {
  if (notConfigured.value) return
  try { await runTest({ upstreamModel: row.baseModel, provider: row.provider, credentialName: row.credentialName, values: {} }) }
  catch (event) { if (event !== 'cancel' && event !== 'close') ElMessage.error('连接测试失败') }
}
async function confirmTest(payload) {
  if (notConfigured.value) return
  try { await runTest(payload) } catch (event) { if (event !== 'cancel' && event !== 'close') ElMessage.error('连接测试失败') }
}

async function save(payload) {
  if (notConfigured.value) return
  saving.value = true
  try { await updateEngineDeployment(editing.value.deploymentId, payload); ElMessage.success('部署已更新'); drawerOpen.value = false; await load() }
  catch { ElMessage.error('部署更新失败') } finally { saving.value = false }
}

async function remove(row) {
  if (notConfigured.value) return
  try {
    await ElMessageBox.confirm(`确认删除部署 ${row.alias}？`, '删除部署', { type: 'warning' })
    await deleteEngineDeployment(row.deploymentId); ElMessage.success('部署已删除'); await load()
  } catch (event) { if (event !== 'cancel' && event !== 'close') ElMessage.error('部署删除失败') }
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-height: 360px; }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { color: var(--el-text-color-secondary); font-size: 12px; }
.row-actions { display: flex; justify-content: flex-end; gap: 5px; }
.mobile-actions { display: none; }
code { font-family: var(--ai-font-mono); font-size: 12px; }
@media (max-width: 760px) {
  :deep(.mobile-hidden), .desktop-actions { display: none; }
  .mobile-actions { display: inline-flex; }
}
</style>
