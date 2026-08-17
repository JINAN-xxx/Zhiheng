<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">
        <el-button v-if="canAdd" type="primary" plain :icon="Plus" :disabled="notConfigured" @click="openCreate">新建凭据</el-button>
        <span v-if="lastSuccessAt">更新于 {{ formatTime(lastSuccessAt) }}</span>
      </div>
      <el-tooltip content="刷新 Provider 凭据" placement="top"><el-button circle :icon="Refresh" :loading="loading" aria-label="刷新 Provider 凭据" @click="load" /></el-tooltip>
    </div>
    <el-alert v-if="notConfigured" :title="rows.length ? 'Management Key 未配置，当前数据可能已过期' : 'Management Key 未配置'" type="warning" :closable="false" show-icon />
    <el-alert v-else-if="error && rows.length" title="刷新失败，数据可能已过期" type="warning" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-alert v-else-if="error" title="Provider 凭据加载失败" type="error" :closable="false" show-icon>
      <template #default><el-button link type="primary" @click="load">重新加载</el-button></template>
    </el-alert>
    <el-table v-if="rows.length || loading" v-loading="loading" :data="rows" table-layout="fixed">
      <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip><template #default="{ row }"><code>{{ row.name }}</code></template></el-table-column>
      <el-table-column v-if="!mobile" prop="provider" label="Provider" min-width="150" />
      <el-table-column v-if="!mobile" prop="deploymentCount" label="引用数" width="80" align="right" />
      <el-table-column v-if="!mobile" label="更新时间" width="168"><template #default="{ row }">{{ formatTime(row.updatedAt) }}</template></el-table-column>
      <el-table-column v-if="hasAnyRowAction" label="操作" :width="mobile ? 86 : 90" align="right"><template #default="{ row }">
        <div class="row-actions desktop-actions">
          <el-tooltip v-if="canRotate" content="轮换" placement="top"><el-button link type="primary" :icon="RefreshRight" aria-label="轮换" @click="openRotate(row)" /></el-tooltip>
          <el-tooltip v-if="canRemove" content="删除" placement="top"><el-button link type="danger" :icon="Delete" aria-label="删除" :disabled="row.deploymentCount > 0" @click="remove(row)" /></el-tooltip>
        </div>
        <el-dropdown class="mobile-actions" trigger="click">
          <el-button link :icon="MoreFilled" aria-label="更多操作">更多</el-button>
          <template #dropdown><el-dropdown-menu>
            <el-dropdown-item v-if="canRotate" @click="openRotate(row)">轮换</el-dropdown-item>
            <el-dropdown-item v-if="canRemove" :disabled="row.deploymentCount > 0" @click="remove(row)">删除</el-dropdown-item>
          </el-dropdown-menu></template>
        </el-dropdown>
      </template></el-table-column>
    </el-table>
    <el-empty v-else-if="!error && !notConfigured" description="暂无 Provider 凭据" :image-size="72" />

    <el-drawer v-model="drawerOpen" :title="mode === 'rotate' ? '轮换 Provider 凭据' : '新建 Provider 凭据'" size="min(620px, 100%)" destroy-on-close>
      <CredentialForm
        :providers="providers"
        :mode="mode"
        :credential-name="editing?.name || ''"
        :provider="editing?.provider || ''"
        :submitting="saving"
        @submit="save"
        @cancel="drawerOpen = false"
      />
    </el-drawer>
  </section>
</template>

<script setup>
import { Delete, MoreFilled, Plus, Refresh, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createEngineCredential, deleteEngineCredential,
  listEngineCredentials, listEngineProviders, rotateEngineCredential,
} from '../../../api/modelEngine.js'
import { createRequestGate, hasPermission, isManagementMisconfiguredError, normalizeCredential, normalizeProvider } from '../../../utils/modelEngine.js'
import CredentialForm from './CredentialForm.vue'

const props = defineProps({
  permissions: { type: Array, default: () => [] },
  mobile: { type: Boolean, default: false },
})

const rows = ref([]); const providers = ref([]); const loading = ref(false); const error = ref(false)
const notConfigured = ref(false); const drawerOpen = ref(false); const saving = ref(false)
const mode = ref('create'); const editing = ref(null)
const lastSuccessAt = ref(null); const providersAvailable = ref(false)
const loadGate = createRequestGate()
const can = (permission) => hasPermission(props.permissions, permission)
const canAdd = computed(() => can('ai:engine:credential:add'))
const canRotate = computed(() => can('ai:engine:credential:rotate'))
const canRemove = computed(() => can('ai:engine:credential:remove'))
const hasAnyRowAction = computed(() => !notConfigured.value && (canRotate.value || canRemove.value))
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'

async function load() {
  const requestGeneration = loadGate.begin()
  loading.value = true; error.value = false
  try {
    const credentialsResponse = await listEngineCredentials()
    if (!loadGate.isCurrent(requestGeneration)) return
    notConfigured.value = false
    rows.value = (credentialsResponse?.data || []).map(normalizeCredential)
    lastSuccessAt.value = new Date().toISOString()
    const providerResult = await Promise.allSettled([listEngineProviders()])
    if (!loadGate.isCurrent(requestGeneration)) return
    providersAvailable.value = providerResult[0].status === 'fulfilled'
    if (providerResult[0].status === 'fulfilled') providers.value = (providerResult[0].value?.data || []).map(normalizeProvider).filter(Boolean)
  } catch (event) {
    if (!loadGate.isCurrent(requestGeneration)) return
    const managementMisconfigured = isManagementMisconfiguredError(event)
    if (managementMisconfigured) notConfigured.value = true
    error.value = !managementMisconfigured
    if (managementMisconfigured) { drawerOpen.value = false; editing.value = null }
  }
  finally { if (loadGate.isCurrent(requestGeneration)) loading.value = false }
}

function openForm(nextMode, row = null) {
  if (notConfigured.value) return
  if (!providersAvailable.value) { ElMessage.error('Provider 选项加载失败，请刷新后重试'); return }
  mode.value = nextMode; editing.value = row; drawerOpen.value = true
}
function openCreate() { openForm('create') }
function openRotate(row) { openForm('rotate', row) }

async function save(payload) {
  if (notConfigured.value) return
  saving.value = true
  try {
    if (mode.value === 'rotate') await rotateEngineCredential(payload.credentialName, { provider: payload.provider, values: payload.values })
    else await createEngineCredential(payload)
    ElMessage.success(mode.value === 'rotate' ? '凭据已轮换' : '凭据已创建')
    drawerOpen.value = false; await load()
  } catch { ElMessage.error(mode.value === 'rotate' ? '凭据轮换失败' : '凭据创建失败') }
  finally { saving.value = false }
}

async function remove(row) {
  if (notConfigured.value) return
  try {
    await ElMessageBox.confirm(`确认删除凭据 ${row.name}？`, '删除凭据', { type: 'warning' })
    await deleteEngineCredential(row.name); ElMessage.success('凭据已删除'); await load()
  } catch (event) { if (event !== 'cancel' && event !== 'close') ElMessage.error('凭据删除失败') }
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-height: 360px; }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { display: flex; align-items: center; gap: 10px; color: var(--el-text-color-secondary); font-size: 12px; }
.row-actions { display: flex; justify-content: flex-end; gap: 5px; }
.mobile-actions { display: none; }
code { font-family: var(--ai-font-mono); font-size: 12px; }
@media (max-width: 760px) {
  :deep(.mobile-hidden), .desktop-actions { display: none; }
  .mobile-actions { display: inline-flex; }
}
</style>
