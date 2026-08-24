<template>
  <section class="workspace-panel">
    <div class="panel-toolbar">
      <div class="panel-summary">
        <a-button v-if="canAdd" type="primary" ghost :disabled="notConfigured" @click="openCreate"><template #icon><PlusOutlined /></template>新建凭据</a-button>
        <span v-if="lastSuccessAt">更新于 {{ formatTime(lastSuccessAt) }}</span>
      </div>
      <a-tooltip title="刷新服务商凭据" placement="top"><a-button shape="circle" :loading="loading" aria-label="刷新服务商凭据" @click="load"><template #icon><ReloadOutlined /></template></a-button></a-tooltip>
    </div>
    <a-alert v-if="notConfigured" :message="rows.length ? '管理密钥未配置，当前数据可能已过期' : '管理密钥未配置'" type="warning" show-icon />
    <a-alert v-else-if="error && rows.length" message="刷新失败，数据可能已过期" type="warning" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-alert v-else-if="error" message="服务商凭据加载失败" type="error" show-icon>
      <template #action><a-button type="link" size="small" @click="load">重新加载</a-button></template>
    </a-alert>
    <a-table v-if="rows.length || loading" class="model-table" :columns="columns" :data-source="rows" :loading="loading" :pagination="false" row-key="name" table-layout="fixed">
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'name'"><code :title="row.name">{{ row.name }}</code></template>
        <template v-else-if="column.key === 'updatedAt'">{{ formatTime(row.updatedAt) }}</template>
        <template v-else-if="column.key === 'actions'">
          <div class="row-actions desktop-actions">
            <a-tooltip v-if="canRotate" title="轮换" placement="top"><a-button type="link" aria-label="轮换" @click="openRotate(row)"><template #icon><SyncOutlined /></template></a-button></a-tooltip>
            <a-tooltip v-if="canRemove" title="删除" placement="top"><a-button type="link" danger aria-label="删除" :disabled="row.deploymentCount > 0" @click="remove(row)"><template #icon><DeleteOutlined /></template></a-button></a-tooltip>
          </div>
          <a-dropdown class="mobile-actions" :trigger="['click']">
            <a-button type="text" aria-label="更多操作"><template #icon><EllipsisOutlined /></template></a-button>
            <template #overlay><a-menu @click="({ key }) => handleRowAction(key, row)">
              <a-menu-item v-if="canRotate" key="rotate">轮换</a-menu-item>
              <a-menu-item v-if="canRemove" key="remove" :disabled="row.deploymentCount > 0" danger>删除</a-menu-item>
            </a-menu></template>
          </a-dropdown>
        </template>
      </template>
    </a-table>
    <a-empty v-else-if="!error && !notConfigured" description="暂无服务商凭据" :image-style="{ height: '72px' }" />

    <a-drawer v-model:open="drawerOpen" root-class-name="model-form-drawer" :title="mode === 'rotate' ? '轮换服务商凭据' : '新建服务商凭据'" width="min(620px, 100%)" destroy-on-close>
      <CredentialForm
        :providers="providers"
        :mode="mode"
        :credential-name="editing?.name || ''"
        :provider="editing?.provider || ''"
        :submitting="saving"
        @submit="save"
        @cancel="drawerOpen = false"
      />
    </a-drawer>
  </section>
</template>

<script setup>
import { DeleteOutlined, EllipsisOutlined, PlusOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
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
const columns = computed(() => [
  { title: '名称', dataIndex: 'name', key: 'name', width: props.mobile ? 180 : 220, ellipsis: true },
  !props.mobile && { title: '服务商', dataIndex: 'provider', key: 'provider', width: 150 },
  !props.mobile && { title: '引用数', dataIndex: 'deploymentCount', key: 'deploymentCount', width: 80, align: 'right' },
  !props.mobile && { title: '更新时间', key: 'updatedAt', width: 168 },
  hasAnyRowAction.value && { title: '操作', key: 'actions', width: props.mobile ? 64 : 96, align: 'right' },
].filter(Boolean))
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
  if (!providersAvailable.value) { feedback.msgError('服务商选项加载失败，请刷新后重试'); return }
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
    feedback.msgSuccess(mode.value === 'rotate' ? '凭据已轮换' : '凭据已创建')
    drawerOpen.value = false; await load()
  } catch { feedback.msgError(mode.value === 'rotate' ? '凭据轮换失败' : '凭据创建失败') }
  finally { saving.value = false }
}

async function remove(row) {
  if (notConfigured.value) return
  try {
    await feedback.confirm(`确认删除凭据 ${row.name}？`)
    await deleteEngineCredential(row.name); feedback.msgSuccess('凭据已删除'); await load()
  } catch (event) { if (event !== 'cancel' && event !== 'close') feedback.msgError('凭据删除失败') }
}

function handleRowAction(action, row) {
  const actions = { rotate: openRotate, remove }
  actions[action]?.(row)
}

onMounted(load)
onBeforeUnmount(() => loadGate.invalidate())
defineExpose({ refresh: load })
</script>

<style scoped>
.workspace-panel { min-height: 360px; color: var(--color-text); }
.panel-toolbar { display: flex; align-items: center; justify-content: space-between; min-height: 40px; margin-bottom: 10px; }
.panel-summary { display: flex; align-items: center; gap: 10px; color: var(--color-text-secondary); font-size: 12px; }
.model-table :deep(.ant-table-container) { overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; }
.model-table :deep(.ant-table-thead > tr > th) { height: 44px; border-bottom-color: var(--gray-150); background: var(--gray-10); color: var(--color-text-secondary); }
.model-table :deep(.ant-table-tbody > tr > td) { height: 46px; border-bottom-color: var(--gray-150); background: var(--gray-0); color: var(--color-text); }
.model-table :deep(.ant-table-tbody > tr:hover > td) { background: var(--gray-50) !important; }
.row-actions { display: flex; justify-content: flex-end; gap: 5px; }
.mobile-actions { display: none; }
code { font-family: var(--ai-font-mono); color: var(--color-text); font-size: 12px; }
:global(.model-form-drawer .ant-drawer-content) { background: var(--gray-0); }
:global(.model-form-drawer .ant-drawer-header) { border-bottom: 1px solid var(--gray-150); background: var(--gray-0); }
@media (max-width: 760px) {
  .desktop-actions { display: none; }
  .mobile-actions { display: inline-flex; }
}
</style>
