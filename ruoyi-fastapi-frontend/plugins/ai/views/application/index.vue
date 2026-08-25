<template>
  <main class="app-container developer-api-page">
    <WorkspaceToolbar title="开发接入" subtitle="管理应用、模型授权、访问密钥与调用记录">
      <template #actions>
        <a-tooltip title="刷新应用" placement="bottom">
          <a-button shape="circle" aria-label="刷新应用" @click="refreshCurrentView"><template #icon><ReloadOutlined /></template></a-button>
        </a-tooltip>
        <a-button v-if="can('ai:application:add')" type="primary" @click="openApplicationForm()"><PlusOutlined />新建应用</a-button>
      </template>
    </WorkspaceToolbar>

    <section class="developer-grid">
      <WorkspacePanel title="应用" subtitle="选择应用后管理接入配置" flush>
        <div class="application-tools">
          <a-input v-model:value="query.applicationName" allow-clear placeholder="搜索应用" @press-enter="loadApplications()">
            <template #prefix><SearchOutlined /></template>
          </a-input>
        </div>
        <WorkspaceState v-if="loading && !applications.length" kind="loading" />
        <WorkspaceState v-else-if="error && !applications.length" kind="error" :message="error" @retry="loadApplications()" />
        <WorkspaceState v-else-if="!applications.length" kind="empty" title="暂无开发者应用" />
        <template v-else>
          <div v-if="error" class="stale-notice">刷新失败，当前列表可能已过期。</div>
          <ResourceTable :columns="applicationColumns" :rows="applications" :pagination="false" row-key="applicationId">
            <template #bodyCell="{ column, record: application }">
              <button
                v-if="column.key === 'application'"
                type="button"
                class="application-row"
                :class="{ 'is-active': application.applicationId === selectedApplication?.applicationId }"
                @click="selectApplication(application)"
              >
                <span class="application-row__main"><strong>{{ application.applicationName }}</strong><small>{{ application.applicationCode }}</small></span>
                <WorkspaceStatus :status="application.status" :label="applicationStatusLabel(application.status)" />
              </button>
            </template>
          </ResourceTable>
          <a-pagination
            v-if="total > query.pageSize"
            v-model:current="query.pageNum"
            v-model:page-size="query.pageSize"
            :total="total"
            :page-size-options="['10', '20', '50']"
            show-size-changer
            size="small"
            @change="loadApplications()"
          />
        </template>
      </WorkspacePanel>

      <section class="application-detail">
        <WorkspaceState v-if="!selectedApplication" kind="empty" title="选择应用以查看详情" />
        <template v-else>
          <header class="detail-header">
            <div class="detail-identity">
              <div><h2>{{ selectedApplication.applicationName }}</h2><code>{{ selectedApplication.applicationCode }}</code></div>
              <WorkspaceStatus :status="selectedApplication.status" :label="applicationStatusLabel(selectedApplication.status)" />
            </div>
            <div class="detail-actions">
              <a-button v-if="can('ai:application:edit') && selectedApplication.status === 'active'" type="text" @click="openApplicationForm(selectedApplication)"><EditOutlined />编辑</a-button>
              <a-button v-if="can('ai:application:disable') && selectedApplication.status === 'active'" type="text" danger @click="disableSelectedApplication"><StopOutlined />停用</a-button>
              <a-button v-if="can('ai:application:enable') && selectedApplication.status === 'disabled'" type="primary" ghost @click="enableSelectedApplication"><PlayCircleOutlined />重新启用</a-button>
              <a-button v-if="can('ai:application:delete') && selectedApplication.status === 'disabled'" type="text" danger @click="deleteSelectedApplication"><DeleteOutlined />删除应用</a-button>
            </div>
          </header>

          <a-tabs class="detail-tabs" :active-key="activeTab" @change="changeTab">
            <a-tab-pane key="overview" tab="概览" />
            <a-tab-pane key="keys" tab="访问密钥" />
            <a-tab-pane key="models" tab="模型授权" />
            <a-tab-pane key="usage" tab="调用记录" />
          </a-tabs>

          <ApplicationOverviewPanel v-if="activeTab === 'overview'" :application="selectedApplication" />
          <ApplicationKeysPanel
            v-else-if="activeTab === 'keys'"
            :keys="keys"
            :loading="keyLoading"
            :error="keyError"
            :can-list="canListKeys"
            :can-create="canCreateKey && selectedApplication.status === 'active'"
            :actions="keyActions"
            @create="openKeyForm"
            @retry="loadKeys"
            @command="handleKeyAction"
          />
          <ApplicationModelsPanel
            v-else-if="activeTab === 'models'"
            :keys="keys"
            :models="models"
            :loading="keyLoading || modelLoading"
            :error="keyError || modelError"
            :can-list="canListKeys"
            @retry="loadModelAuthorizations"
          />
          <ApplicationUsagePanel
            v-else
            :rows="usageRows"
            :total="usageTotal"
            :loading="usageLoading"
            :error="usageError"
            :can-query="canQueryUsage"
            :page-num="usageQuery.pageNum"
            :page-size="usageQuery.pageSize"
            @retry="loadUsage"
            @page-change="changeUsagePage"
          />
        </template>
      </section>
    </section>

    <a-modal v-model:open="applicationFormOpen" :title="editingApplication ? '编辑应用' : '新建应用'" :width="500" destroy-on-close>
      <a-form ref="applicationFormRef" :model="applicationForm" :rules="applicationRules" layout="vertical">
        <a-form-item v-if="!editingApplication" label="应用编码" name="applicationCode"><a-input v-model:value="applicationForm.applicationCode" :maxlength="100" placeholder="例如 internal-tools" /></a-form-item>
        <a-form-item label="应用名称" name="applicationName"><a-input v-model:value="applicationForm.applicationName" :maxlength="100" placeholder="例如内部智能工具" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="applicationFormOpen = false">取消</a-button><a-button type="primary" :loading="saving" @click="saveApplication">保存</a-button></template>
    </a-modal>

    <a-modal v-model:open="keyFormOpen" title="创建访问密钥" :width="600" destroy-on-close :after-close="handleKeyFormClosed">
      <a-form ref="keyFormRef" :model="keyForm" :rules="keyRules" layout="vertical">
        <a-form-item label="密钥名称" name="keyName"><a-input v-model:value="keyForm.keyName" :maxlength="100" /></a-form-item>
        <a-form-item label="授权模型" name="modelIds"><a-select v-model:value="keyForm.modelIds" mode="multiple" show-search option-filter-prop="label" style="width: 100%" placeholder="至少选择一个已发布模型" :options="modelOptions" /></a-form-item>
        <div class="quota-grid">
          <a-form-item label="每分钟请求数"><a-input-number v-model:value="keyForm.rpmLimit" :min="1" style="width: 100%" /></a-form-item>
          <a-form-item label="每分钟令牌数"><a-input-number v-model:value="keyForm.tpmLimit" :min="1" style="width: 100%" /></a-form-item>
          <a-form-item label="并发数"><a-input-number v-model:value="keyForm.concurrencyLimit" :min="1" style="width: 100%" /></a-form-item>
        </div>
        <a-form-item label="过期时间"><a-date-picker v-model:value="keyForm.expiresAt" show-time style="width: 100%" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="keyFormOpen = false">取消</a-button><a-button type="primary" :loading="saving" @click="saveKey">创建</a-button></template>
    </a-modal>

    <a-modal v-model:open="issuedKeyOpen" title="访问密钥已生成" :width="560" :mask-closable="false" :after-close="clearIssuedKey">
      <a-alert type="warning" :closable="false" message="该密钥仅展示一次，关闭后无法再次查看。" show-icon />
      <div class="issued-key"><code>{{ issuedKey?.apiKey }}</code><a-button aria-label="复制访问密钥" title="复制访问密钥" @click="copyIssuedKey"><template #icon><CopyOutlined /></template></a-button></div>
      <template #footer><a-button type="primary" @click="issuedKeyOpen = false">我已保存</a-button></template>
    </a-modal>
  </main>
</template>

<script setup name="AiApplication">
import { CopyOutlined, DeleteOutlined, EditOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import useUserStore from '@/store/modules/user'
import feedback from '@/plugins/antdFeedback'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspacePanel from '@/components/Workspace/WorkspacePanel.vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceStatus from '@/components/Workspace/WorkspaceStatus.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { listPlatformModels } from '../../api/modelCatalog.js'
import {
  createApplication, createDeveloperKey, deleteApplication, disableApplication, enableApplication, getApplication, listApplications,
  listApplicationUsage, listDeveloperKeys, revokeDeveloperKey, rotateDeveloperKey, updateApplication,
} from '../../api/application.js'
import {
  applicationStatusLabel, applicationTabResources, buildDeveloperKeyPayload, extractIssuedKey, isCallablePlatformModel, normalizeApplication,
  normalizeApplicationModelPage, normalizeApplicationPage, normalizeApplicationTab, normalizeDeveloperKeys, resolvePreferredApplication, shouldResetDeveloperKeys,
} from '../../utils/application.js'
import { collectAllPages, hasPermission } from '../../utils/modelEngine.js'
import ApplicationKeysPanel from './components/ApplicationKeysPanel.vue'
import ApplicationModelsPanel from './components/ApplicationModelsPanel.vue'
import ApplicationOverviewPanel from './components/ApplicationOverviewPanel.vue'
import ApplicationUsagePanel from './components/ApplicationUsagePanel.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const can = (permission) => hasPermission(userStore.permissions || [], permission)
const canListKeys = computed(() => can('ai:application:key:list'))
const canCreateKey = computed(() => can('ai:application:key:add') && can('ai:model:list'))
const canQueryUsage = computed(() => can('ai:application:query'))
const activeTab = computed(() => normalizeApplicationTab(route.query.tab))
const query = reactive({ applicationName: '', pageNum: 1, pageSize: 10 })
const applications = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const selectedApplication = ref(null)
const keys = ref([])
const keyLoading = ref(false)
const keyError = ref('')
const usageRows = ref([])
const usageTotal = ref(0)
const usageLoading = ref(false)
const usageError = ref('')
const usageQuery = reactive({ pageNum: 1, pageSize: 10 })
const models = ref([])
const modelLoading = ref(false)
const modelError = ref('')
const applicationFormOpen = ref(false)
const editingApplication = ref(null)
const applicationFormRef = ref()
const applicationForm = reactive({ applicationCode: '', applicationName: '' })
const keyFormOpen = ref(false)
const keyFormRef = ref()
const keyForm = reactive(defaultKeyForm())
const issuedKey = ref(null)
const pendingIssuedKey = ref(null)
const issuedKeyOpen = ref(false)
const saving = ref(false)
let applicationRequest = 0
let keyRequest = 0
let usageRequest = 0
let modelRequest = 0

const applicationRules = {
  applicationCode: [
    { required: true, message: '应用编码不能为空', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/, message: '仅支持字母、数字、点、下划线和短横线', trigger: 'blur' },
  ],
  applicationName: [{ required: true, message: '应用名称不能为空', trigger: 'blur' }],
}
const keyRules = {
  keyName: [{ required: true, message: '密钥名称不能为空', trigger: 'blur' }],
  modelIds: [{ type: 'array', required: true, min: 1, message: '至少选择一个模型', trigger: 'change' }],
}
const applicationColumns = [{ title: '应用', key: 'application' }]
const keyActions = computed(() => [
  can('ai:application:key:rotate') && { command: 'rotate', label: '轮换密钥', icon: ReloadOutlined },
  can('ai:application:key:revoke') && { command: 'revoke', label: '撤销密钥', icon: DeleteOutlined, danger: true, divided: true },
].filter(Boolean))
const modelOptions = computed(() => models.value.filter(isCallablePlatformModel).map((model) => ({
  label: model.displayName || model.display_name || model.modelName || model.model_name || model.title || model.modelCode || model.model_code,
  value: model.modelId || model.model_id,
})))

function defaultKeyForm() {
  return { keyName: '', modelIds: [], expiresAt: null, rpmLimit: null, tpmLimit: null, concurrencyLimit: null }
}

async function syncRoute(applicationId, tab = activeTab.value) {
  const nextQuery = { ...route.query, applicationId: applicationId || undefined, tab }
  if (route.query.applicationId === nextQuery.applicationId && route.query.tab === tab) return
  await router.replace({ path: route.path, query: nextQuery })
}

async function changeTab(tab) {
  await syncRoute(selectedApplication.value?.applicationId, normalizeApplicationTab(tab))
}

async function refreshCurrentView() {
  await loadApplications(selectedApplication.value?.applicationId)
}

async function loadApplications(preferredId = route.query.applicationId || selectedApplication.value?.applicationId) {
  const requestId = ++applicationRequest
  loading.value = true
  error.value = ''
  try {
    const page = normalizeApplicationPage(await listApplications(query))
    if (requestId !== applicationRequest) return
    applications.value = page.rows
    total.value = page.total
    const next = await resolvePreferredApplication(page.rows, preferredId, getApplication)
    if (requestId !== applicationRequest) return
    await selectApplication(next, true, 'load')
  } catch (reason) {
    if (requestId === applicationRequest) error.value = reason?.message || '应用列表加载失败'
  } finally {
    if (requestId === applicationRequest) loading.value = false
  }
}

async function selectApplication(application, forceRefresh = false, source = 'manual') {
  if (source === 'manual') {
    applicationRequest += 1
    loading.value = false
  }
  const currentApplicationId = selectedApplication.value?.applicationId || null
  const nextApplicationId = application?.applicationId || null
  const changed = shouldResetDeveloperKeys(currentApplicationId, nextApplicationId)
  if (changed) clearApplicationResources()
  selectedApplication.value = application
  await syncRoute(nextApplicationId)
  if (changed || forceRefresh) await loadActiveTabData()
}

function clearApplicationResources() {
  keyRequest += 1
  usageRequest += 1
  modelRequest += 1
  keys.value = []
  keyError.value = ''
  usageRows.value = []
  usageQuery.pageNum = 1
  usageError.value = ''
  models.value = []
  modelError.value = ''
}

async function loadActiveTabData() {
  if (!selectedApplication.value) return
  const resources = applicationTabResources(activeTab.value)
  if (resources.includes('models')) await loadModelAuthorizations()
  else if (resources.includes('keys')) await loadKeys()
  else if (resources.includes('usage')) await loadUsage()
}

async function loadKeys() {
  if (!selectedApplication.value || !canListKeys.value) return
  const applicationId = selectedApplication.value.applicationId
  const requestId = ++keyRequest
  keyLoading.value = true
  keyError.value = ''
  try {
    const response = await listDeveloperKeys(applicationId)
    if (requestId !== keyRequest || applicationId !== selectedApplication.value?.applicationId) return
    keys.value = normalizeDeveloperKeys(response)
  } catch (reason) {
    if (requestId === keyRequest) keyError.value = reason?.message || '访问密钥加载失败'
  } finally {
    if (requestId === keyRequest) keyLoading.value = false
  }
}

async function loadModels() {
  if (!can('ai:model:list')) return
  const requestId = ++modelRequest
  modelLoading.value = true
  modelError.value = ''
  try {
    const modelRows = await collectAllPages(
      async ({ pageNum, pageSize }) => normalizeApplicationModelPage(await listPlatformModels({ pageNum, pageSize })),
      { pageSize: 100 },
    )
    if (requestId !== modelRequest) return
    models.value = modelRows
  } catch (reason) {
    if (requestId === modelRequest) modelError.value = reason?.message || '模型目录加载失败'
  } finally {
    if (requestId === modelRequest) modelLoading.value = false
  }
}

async function loadModelAuthorizations() {
  await Promise.all([loadKeys(), loadModels()])
}

async function loadUsage() {
  if (!selectedApplication.value || !canQueryUsage.value) return
  const applicationId = selectedApplication.value.applicationId
  const requestId = ++usageRequest
  usageLoading.value = true
  usageError.value = ''
  try {
    const response = await listApplicationUsage(applicationId, { pageNum: usageQuery.pageNum, pageSize: usageQuery.pageSize })
    if (requestId !== usageRequest || applicationId !== selectedApplication.value?.applicationId) return
    const page = response?.data || response || {}
    usageRows.value = Array.isArray(page.rows) ? page.rows : []
    usageTotal.value = Number(page.total) || usageRows.value.length
  } catch (reason) {
    if (requestId === usageRequest) usageError.value = reason?.message || '调用记录加载失败'
  } finally {
    if (requestId === usageRequest) usageLoading.value = false
  }
}

async function changeUsagePage(page, pageSize) {
  usageQuery.pageNum = page
  usageQuery.pageSize = pageSize
  await loadUsage()
}

function openApplicationForm(application = null) {
  if (!application && !can('ai:application:add')) return
  if (application && !can('ai:application:edit')) return
  editingApplication.value = application
  applicationForm.applicationCode = application?.applicationCode || ''
  applicationForm.applicationName = application?.applicationName || ''
  applicationFormOpen.value = true
}

async function saveApplication() {
  if (editingApplication.value && !can('ai:application:edit')) return
  if (!editingApplication.value && !can('ai:application:add')) return
  if (!await applicationFormRef.value.validate().then(() => true).catch(() => false)) return
  saving.value = true
  try {
    let preferredId = selectedApplication.value?.applicationId
    if (editingApplication.value) {
      const response = await updateApplication(editingApplication.value.applicationId, { applicationName: applicationForm.applicationName, expectedVersion: editingApplication.value.version })
      preferredId = normalizeApplication(response.data).applicationId
    } else {
      const response = await createApplication({ ...applicationForm })
      preferredId = normalizeApplication(response.data).applicationId
    }
    applicationFormOpen.value = false
    await loadApplications(preferredId)
  } finally {
    saving.value = false
  }
}

async function disableSelectedApplication() {
  if (!can('ai:application:disable') || selectedApplication.value?.status !== 'active') return
  await feedback.confirm('停用后，该应用下所有访问密钥将无法调用。是否继续？')
  await disableApplication(selectedApplication.value.applicationId)
  await loadApplications(selectedApplication.value.applicationId)
}

async function enableSelectedApplication() {
  if (!can('ai:application:enable') || selectedApplication.value?.status !== 'disabled') return
  await feedback.confirm('重新启用后，该应用下仍然有效的访问密钥可继续调用。是否继续？')
  await enableApplication(selectedApplication.value.applicationId)
  await loadApplications(selectedApplication.value.applicationId)
}

async function deleteSelectedApplication() {
  if (!can('ai:application:delete') || selectedApplication.value?.status !== 'disabled') return
  await feedback.confirm('仅停用应用可以删除。删除后应用将不再显示，历史审计与调用记录仍会保留。是否继续？')
  await deleteApplication(selectedApplication.value.applicationId)
  await loadApplications(null)
}

async function openKeyForm() {
  if (!canCreateKey.value || selectedApplication.value?.status !== 'active') return
  await loadModels()
  keyFormOpen.value = true
}

function resetKeyForm() {
  Object.assign(keyForm, defaultKeyForm())
}

function handleKeyFormClosed() {
  resetKeyForm()
  if (!pendingIssuedKey.value) return
  issuedKey.value = pendingIssuedKey.value
  pendingIssuedKey.value = null
  issuedKeyOpen.value = true
}

async function saveKey() {
  if (!canCreateKey.value || selectedApplication.value?.status !== 'active') return
  if (!await keyFormRef.value.validate().then(() => true).catch(() => false)) return
  saving.value = true
  try {
    const payload = buildDeveloperKeyPayload({ ...keyForm, expiresAt: keyForm.expiresAt?.toDate?.() || keyForm.expiresAt })
    const response = await createDeveloperKey(selectedApplication.value.applicationId, payload)
    pendingIssuedKey.value = extractIssuedKey(response)
    keyFormOpen.value = false
    await loadKeys()
  } finally {
    saving.value = false
  }
}

async function handleKeyAction(command, item) {
  if (item?.status !== 'active') return
  if (command === 'rotate' && !can('ai:application:key:rotate')) return
  if (command === 'revoke' && !can('ai:application:key:revoke')) return
  if (command === 'rotate') {
    await feedback.confirm('轮换后旧密钥将保留 24 小时宽限期，是否继续？')
    const response = await rotateDeveloperKey(selectedApplication.value.applicationId, item.keyId, { gracePeriodSeconds: 86400 })
    showIssuedKey(response)
    await loadKeys()
  } else if (command === 'revoke') {
    await feedback.confirm('撤销后该密钥立即失效，是否继续？')
    await revokeDeveloperKey(selectedApplication.value.applicationId, item.keyId)
    await loadKeys()
  }
}

function showIssuedKey(response) {
  issuedKey.value = extractIssuedKey(response)
  issuedKeyOpen.value = true
}

function clearIssuedKey() {
  issuedKey.value = null
}

async function copyIssuedKey() {
  if (!issuedKey.value?.apiKey) return
  await navigator.clipboard.writeText(issuedKey.value.apiKey)
  feedback.msgSuccess('访问密钥已复制')
}

watch(() => route.query.tab, loadActiveTabData)
watch(() => route.query.applicationId, async (applicationId) => {
  if (applicationId === selectedApplication.value?.applicationId) return
  await loadApplications(applicationId || null)
})
onMounted(() => loadApplications())
onBeforeUnmount(() => {
  applicationRequest += 1
  keyRequest += 1
  usageRequest += 1
  modelRequest += 1
  pendingIssuedKey.value = null
  clearIssuedKey()
})
</script>

<style scoped lang="scss">
.developer-api-page {
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}

.developer-grid {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 16px;
  min-height: 600px;
}

.developer-grid > :deep(.workspace-panel),
.application-detail {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}

.application-tools {
  padding: 12px;
  border-bottom: 1px solid var(--gray-150);
}

.application-row {
  display: flex;
  width: 100%;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border: 0;
  border-bottom: 1px solid var(--gray-150);
  background: transparent;
  color: var(--color-text);
  text-align: left;
  cursor: pointer;
}

.application-row:hover {
  background: var(--gray-50);
  color: var(--color-text);
}

.application-row.is-active {
  background: var(--main-50);
  color: var(--main-700);
}

.application-row.is-active:hover {
  background: var(--main-30);
  color: var(--main-700);
}

.application-row__main,
.detail-identity > div {
  display: grid;
  min-width: 0;
  gap: 4px;
}

.application-row__main strong,
.application-row__main small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.application-row__main small,
.detail-identity code {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.application-detail {
  min-width: 0;
}

.detail-header,
.detail-identity,
.detail-actions {
  display: flex;
  align-items: center;
}

.detail-header {
  min-height: 76px;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--gray-150);
}

.detail-identity,
.detail-actions {
  gap: 12px;
}

.detail-identity h2 {
  margin: 0;
  font-size: 17px;
}

.detail-identity code,
.issued-key code {
  font-family: var(--workspace-font-mono);
}

.detail-tabs {
  padding: 0 18px;
  border-bottom: 1px solid var(--gray-150);
}

.detail-tabs :deep(.ant-tabs-nav) {
  margin: 0;
}

.stale-notice {
  padding: 8px 14px;
  color: var(--color-warning-900);
  background: var(--color-warning-50);
  font-size: 12px;
}

.quota-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.issued-key {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}

.issued-key code {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
