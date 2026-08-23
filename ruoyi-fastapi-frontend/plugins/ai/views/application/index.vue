<template>
  <main class="app-container developer-api-page">
    <header class="developer-header">
      <div>
        <p>管理开发接入应用、模型授权与访问密钥</p>
      </div>
      <div class="developer-header__actions">
        <a-tooltip title="刷新应用" placement="bottom">
          <a-button shape="circle" aria-label="刷新应用" @click="loadApplications">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button v-if="can('ai:application:add')" type="primary" @click="openApplicationForm()"><PlusOutlined /><span class="button-label">新建应用</span></a-button>
      </div>
    </header>

    <section class="developer-grid">
      <WorkspacePanel title="应用" subtitle="每个应用拥有独立访问密钥与模型权限" flush>
        <div class="application-tools">
          <a-input v-model:value="query.applicationName" allow-clear placeholder="搜索应用" @press-enter="loadApplications">
            <template #prefix><SearchOutlined /></template>
          </a-input>
        </div>
        <WorkspaceState v-if="loading && !applications.length" kind="loading" />
        <WorkspaceState v-else-if="error && !applications.length" kind="error" :message="error" @retry="loadApplications" />
        <WorkspaceState v-else-if="!applications.length" kind="empty" title="暂无开发者应用" />
        <template v-else>
          <div v-if="error" class="stale-notice">刷新失败，当前列表可能已过期。</div>
          <ResourceTable
            class="application-table"
            :columns="applicationColumns"
            :rows="applications"
            :pagination="false"
            row-key="applicationId"
          >
            <template #bodyCell="{ column, record: application }">
              <button
                v-if="column.key === 'application'"
                type="button"
                class="application-row"
                :class="{ 'is-active': application.applicationId === selectedApplication?.applicationId }"
                @click="selectApplication(application)"
              >
                <span class="application-row__main">
                  <strong>{{ application.applicationName }}</strong>
                  <small>{{ application.applicationCode }}</small>
                </span>
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
            @change="loadApplications"
          />
        </template>
      </WorkspacePanel>

      <WorkspacePanel
        class="key-panel"
        :title="selectedApplication?.applicationName || '访问密钥'"
        :subtitle="selectedApplication ? selectedApplication.applicationCode : '先选择一个应用'"
        flush
      >
        <template #header>
          <div v-if="selectedApplication" class="key-toolbar">
            <a-button v-if="can('ai:application:edit')" class="secondary-key-action" type="text" aria-label="编辑应用" title="编辑应用" @click="openApplicationForm(selectedApplication)"><EditOutlined /><span class="button-label">编辑</span></a-button>
            <a-button
              v-if="can('ai:application:disable') && selectedApplication.status === 'active'"
              type="text"
              danger
              class="secondary-key-action"
              aria-label="停用应用"
              title="停用应用"
              @click="disableSelectedApplication"
            ><StopOutlined /><span class="button-label">停用</span></a-button>
            <a-button
              v-if="canCreateKey && selectedApplication.status === 'active'"
              type="primary"
              @click="openKeyForm"
            ><KeyOutlined />创建密钥</a-button>
          </div>
        </template>

        <WorkspaceState v-if="!selectedApplication" kind="empty" title="选择应用以查看访问密钥" />
        <WorkspaceState v-else-if="!canListKeys" kind="forbidden" title="无权查看访问密钥" message="当前账号缺少访问密钥列表权限。" />
        <WorkspaceState v-else-if="keyLoading && !keys.length" kind="loading" />
        <WorkspaceState v-else-if="keyError && !keys.length" kind="error" :message="keyError" @retry="loadKeys" />
        <WorkspaceState v-else-if="!keys.length" kind="empty" title="暂无访问密钥" message="为当前应用创建第一个访问密钥。" />
        <div v-else class="key-list">
          <div v-if="keyError" class="stale-notice">刷新失败，当前访问密钥列表可能已过期。</div>
          <article v-for="item in keys" :key="item.keyId" class="key-row">
            <div class="key-row__identity">
              <div class="key-row__title">
                <strong>{{ item.keyName }}</strong>
                <WorkspaceStatus :status="item.status" :label="developerKeyStatusLabel(item.status)" />
              </div>
              <code>{{ item.maskedKey || item.fingerprint }}</code>
              <small>{{ item.grants.length }} 个模型授权 · 最近使用 {{ formatTime(item.lastUsedAt) }}</small>
            </div>
            <div class="key-row__quota">
              <span>每分钟请求数 {{ item.quota.rpmLimit ?? '不限' }}</span>
              <span>每分钟令牌数 {{ item.quota.tpmLimit ?? '不限' }}</span>
              <span>并发 {{ item.quota.concurrencyLimit ?? '不限' }}</span>
            </div>
            <WorkspaceMoreMenu
              v-if="item.status === 'active' && (can('ai:application:key:rotate') || can('ai:application:key:revoke'))"
              :items="keyActions"
              @command="handleKeyAction($event, item)"
            />
          </article>
        </div>
      </WorkspacePanel>

      <WorkspacePanel class="usage-panel" :title="`调用记录 · ${selectedApplication?.applicationCode || '-'}`" subtitle="最近调用、令牌与结算记录" flush>
        <WorkspaceState v-if="!selectedApplication" kind="empty" title="选择应用以查看调用记录" />
        <WorkspaceState v-else-if="!canQueryUsage" kind="forbidden" title="无权查看调用记录" message="当前账号缺少应用查询权限。" />
        <WorkspaceState v-else-if="usageLoading && !usageRows.length" kind="loading" />
        <WorkspaceState v-else-if="usageError && !usageRows.length" kind="error" :message="usageError" @retry="loadUsage" />
        <WorkspaceState v-else-if="!usageRows.length" kind="empty" title="暂无调用记录" message="公开接口调用完成后会在此展示。" />
        <div v-else class="usage-list">
          <div v-if="usageError" class="stale-notice">刷新失败，当前用量数据可能已过期。</div>
          <ResourceTable
            :columns="usageColumns"
            :rows="usageRows"
            :pagination="false"
            :scroll="{ x: 860 }"
            :row-key="usageRowKey"
          >
            <template #bodyCell="{ column, record: row }">
              <template v-if="column.key === 'startedAt'">{{ formatTime(row.startedAt) }}</template>
              <template v-else-if="column.key === 'operationType'">
                <a-tag :color="tagColor(operationTypeMeta(row.operationType).type)">{{ operationTypeMeta(row.operationType).label }}</a-tag>
              </template>
              <template v-else-if="column.key === 'tokens'">{{ row.inputTokens ?? '-' }} / {{ row.outputTokens ?? '-' }}</template>
              <template v-else-if="column.key === 'searchUnits'">{{ row.searchUnits ?? '-' }}</template>
              <template v-else-if="column.key === 'billableCost'">
                {{ row.billableCost != null ? Number(row.billableCost).toFixed(6) : '-' }}
                <a-tag v-if="row.costEstimated" color="orange">估算</a-tag>
              </template>
              <template v-else-if="column.key === 'status'"><a-tag>{{ usageStatusLabel(row.status) }}</a-tag></template>
            </template>
          </ResourceTable>
          <a-pagination
            v-if="usageTotal > usageQuery.pageSize"
            v-model:current="usageQuery.pageNum"
            v-model:page-size="usageQuery.pageSize"
            :total="usageTotal"
            :page-size-options="['10', '20', '50']"
            show-size-changer
            size="small"
            @change="loadUsage"
          />
        </div>
      </WorkspacePanel>
    </section>

    <a-modal v-model:open="applicationFormOpen" :title="editingApplication ? '编辑应用' : '新建应用'" :width="500" destroy-on-close>
      <a-form ref="applicationFormRef" :model="applicationForm" :rules="applicationRules" layout="vertical">
        <a-form-item v-if="!editingApplication" label="应用编码" name="applicationCode">
          <a-input v-model:value="applicationForm.applicationCode" :maxlength="100" placeholder="例如 internal-tools" />
        </a-form-item>
        <a-form-item label="应用名称" name="applicationName">
          <a-input v-model:value="applicationForm.applicationName" :maxlength="100" placeholder="例如内部智能工具" />
        </a-form-item>
      </a-form>
      <template #footer>
        <a-button @click="applicationFormOpen = false">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveApplication">保存</a-button>
      </template>
    </a-modal>

    <a-modal v-model:open="keyFormOpen" title="创建访问密钥" :width="600" destroy-on-close :after-close="handleKeyFormClosed">
      <a-form ref="keyFormRef" :model="keyForm" :rules="keyRules" layout="vertical">
        <a-form-item label="密钥名称" name="keyName"><a-input v-model:value="keyForm.keyName" :maxlength="100" /></a-form-item>
        <a-form-item label="授权模型" name="modelIds">
          <a-select
            v-model:value="keyForm.modelIds"
            mode="multiple"
            show-search
            option-filter-prop="label"
            style="width: 100%"
            placeholder="至少选择一个已发布模型"
            :options="modelOptions"
          />
        </a-form-item>
        <div class="quota-grid">
          <a-form-item label="每分钟请求数"><a-input-number v-model:value="keyForm.rpmLimit" :min="1" style="width: 100%" /></a-form-item>
          <a-form-item label="每分钟令牌数"><a-input-number v-model:value="keyForm.tpmLimit" :min="1" style="width: 100%" /></a-form-item>
          <a-form-item label="并发数"><a-input-number v-model:value="keyForm.concurrencyLimit" :min="1" style="width: 100%" /></a-form-item>
        </div>
        <a-form-item label="过期时间"><a-date-picker v-model:value="keyForm.expiresAt" show-time style="width: 100%" /></a-form-item>
      </a-form>
      <template #footer>
        <a-button @click="keyFormOpen = false">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveKey">创建</a-button>
      </template>
    </a-modal>

    <a-modal v-model:open="issuedKeyOpen" title="访问密钥已生成" :width="560" :mask-closable="false" :after-close="clearIssuedKey">
      <a-alert type="warning" :closable="false" message="该密钥仅展示一次，关闭后无法再次查看。" show-icon />
      <div class="issued-key">
        <code>{{ issuedKey?.apiKey }}</code>
        <a-button aria-label="复制访问密钥" title="复制访问密钥" @click="copyIssuedKey"><template #icon><CopyOutlined /></template></a-button>
      </div>
      <template #footer><a-button type="primary" @click="issuedKeyOpen = false">我已保存</a-button></template>
    </a-modal>
  </main>
</template>

<script setup name="AiApplication">
import {
  CopyOutlined, DeleteOutlined, EditOutlined, KeyOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined,
} from '@ant-design/icons-vue'
import useUserStore from '@/store/modules/user'
import feedback from '@/plugins/antdFeedback'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
import WorkspacePanel from '@/components/Workspace/WorkspacePanel.vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceStatus from '@/components/Workspace/WorkspaceStatus.vue'
import { listPlatformModels } from '../../api/modelCatalog.js'
import {
  createApplication,
  createDeveloperKey,
  disableApplication,
  listApplications,
  listApplicationUsage,
  listDeveloperKeys,
  revokeDeveloperKey,
  rotateDeveloperKey,
  updateApplication,
} from '../../api/application.js'
import {
  applicationStatusLabel,
  buildDeveloperKeyPayload,
  developerKeyStatusLabel,
  extractIssuedKey,
  normalizeApplication,
  normalizeApplicationPage,
  normalizeDeveloperKeys,
  shouldResetDeveloperKeys,
} from '../../utils/application.js'
import { hasPermission } from '../../utils/modelEngine.js'

const userStore = useUserStore()
const can = (permission) => hasPermission(userStore.permissions || [], permission)
const canListKeys = computed(() => can('ai:application:key:list'))
const canCreateKey = computed(() => can('ai:application:key:add') && can('ai:model:list'))
const canQueryUsage = computed(() => can('ai:application:query'))
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
const usageQuery = reactive({ operationType: '', status: '', pageNum: 1, pageSize: 10 })
const models = ref([])
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
const keyActions = computed(() => [
  can('ai:application:key:rotate') && { command: 'rotate', label: '轮换密钥', icon: ReloadOutlined },
  can('ai:application:key:revoke') && { command: 'revoke', label: '撤销密钥', icon: DeleteOutlined, danger: true, divided: true },
].filter(Boolean))
const applicationColumns = [
  { title: '应用', key: 'application' },
]
const modelOptions = computed(() => models.value.map((model) => ({
  label: model.title || model.modelCode,
  value: model.modelId,
})))
const usageColumns = [
  { title: '开始时间', dataIndex: 'startedAt', key: 'startedAt', width: 150 },
  { title: '类型', dataIndex: 'operationType', key: 'operationType', width: 110 },
  { title: '模型', dataIndex: 'modelCode', key: 'modelCode', width: 150, ellipsis: true },
  { title: '令牌', key: 'tokens', width: 140 },
  { title: '检索单元', key: 'searchUnits', width: 110, mobileHidden: true },
  { title: '结算成本', key: 'billableCost', width: 130, mobileHidden: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
]

function defaultKeyForm() {
  return { keyName: '', modelIds: [], expiresAt: null, rpmLimit: null, tpmLimit: null, concurrencyLimit: null }
}

function formatTime(value) {
  if (!value) return '从未'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

async function loadApplications() {
  const requestId = ++applicationRequest
  loading.value = true
  error.value = ''
  try {
    const page = normalizeApplicationPage(await listApplications(query))
    if (requestId !== applicationRequest) return
    applications.value = page.rows
    total.value = page.total
    const next = page.rows.find((item) => item.applicationId === selectedApplication.value?.applicationId) || page.rows[0] || null
    await selectApplication(next)
  } catch (reason) {
    if (requestId === applicationRequest) error.value = reason?.message || '应用列表加载失败'
  } finally {
    if (requestId === applicationRequest) loading.value = false
  }
}

async function selectApplication(application) {
  const currentApplicationId = selectedApplication.value?.applicationId || null
  const nextApplicationId = application?.applicationId || null
  if (shouldResetDeveloperKeys(currentApplicationId, nextApplicationId)) {
    ++keyRequest
    keys.value = []
    keyError.value = ''
  }
  if (currentApplicationId !== nextApplicationId) {
    ++usageRequest
    usageRows.value = []
    usageQuery.pageNum = 1
    usageError.value = ''
  }
  selectedApplication.value = application
  if (application && canListKeys.value) await loadKeys()
  if (application && canQueryUsage.value) await loadUsage()
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

const operationTypeMeta = (operationType) => ({
  chat: { label: '对话', type: 'primary' },
  embedding: { label: '向量', type: 'success' },
  rerank: { label: '排序', type: 'warning' },
}[operationType] || { label: operationType || '-', type: 'info' })
const usageStatusLabel = (status) => ({
  completed: '成功', failed: '失败', cancelled: '已取消', abandoned: '已放弃', started: '处理中',
}[status] || status || '-')
const tagColor = (type) => ({ primary: 'blue', success: 'green', warning: 'orange', danger: 'red' }[type])
const usageRowKey = (row) => row.usageId || row.requestId || row.id || `${row.startedAt}-${row.modelCode}`

let usageRequest = 0
async function loadUsage() {
  if (!selectedApplication.value || !canQueryUsage.value) return
  const applicationId = selectedApplication.value.applicationId
  const requestId = ++usageRequest
  usageLoading.value = true
  usageError.value = ''
  try {
    const response = await listApplicationUsage(applicationId, {
      pageNum: usageQuery.pageNum,
      pageSize: usageQuery.pageSize,
      operationType: usageQuery.operationType || undefined,
      status: usageQuery.status || undefined,
    })
    if (requestId !== usageRequest || applicationId !== selectedApplication.value?.applicationId) return
    const page = response?.data || response || {}
    usageRows.value = Array.isArray(page.rows) ? page.rows : []
    usageTotal.value = Number(page.total) || usageRows.value.length
  } catch (reason) {
    if (requestId === usageRequest) usageError.value = reason?.message || '用量台账加载失败'
  } finally {
    if (requestId === usageRequest) usageLoading.value = false
  }
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
  if (!editingApplication.value) {
    if (!can('ai:application:add')) return
  } else if (!can('ai:application:edit')) return
  if (!await applicationFormRef.value.validateFields().then(() => true).catch(() => false)) return
  saving.value = true
  try {
    if (editingApplication.value) {
      const response = await updateApplication(editingApplication.value.applicationId, {
        applicationName: applicationForm.applicationName,
        expectedVersion: editingApplication.value.version,
      })
      selectedApplication.value = normalizeApplication(response.data)
    } else {
      const response = await createApplication({ ...applicationForm })
      selectedApplication.value = normalizeApplication(response.data)
    }
    applicationFormOpen.value = false
    await loadApplications()
  } finally {
    saving.value = false
  }
}

async function disableSelectedApplication() {
  if (!can('ai:application:disable') || selectedApplication.value?.status !== 'active') return
  await feedback.confirm('停用后，该应用下所有访问密钥将无法调用。是否继续？')
  await disableApplication(selectedApplication.value.applicationId)
  await loadApplications()
}

async function openKeyForm() {
  if (!canCreateKey.value) return
  const response = await listPlatformModels({ pageNum: 1, pageSize: 100 })
  models.value = response.rows || response.data?.rows || []
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
  if (!await keyFormRef.value.validateFields().then(() => true).catch(() => false)) return
  saving.value = true
  try {
    const expiresAt = keyForm.expiresAt?.toDate?.() || keyForm.expiresAt
    const response = await createDeveloperKey(selectedApplication.value.applicationId, buildDeveloperKeyPayload({ ...keyForm, expiresAt }))
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

onMounted(loadApplications)
onBeforeUnmount(() => {
  applicationRequest += 1
  keyRequest += 1
  usageRequest += 1
  pendingIssuedKey.value = null
  clearIssuedKey()
})
</script>

<style scoped lang="scss">
.developer-api-page {
  color: var(--workspace-text);
  font-family: var(--workspace-font-family);
}

.developer-header {
  display: flex;
  min-height: 44px;
  align-items: center;
  justify-content: space-between;
  gap: var(--workspace-space-4);
  margin-bottom: var(--workspace-space-4);
}

.developer-header p {
  margin: 0;
  color: var(--workspace-text-secondary);
  font-size: 13px;
}

.developer-header__actions,
.key-toolbar,
.key-row__title,
.key-row__quota {
  display: flex;
  align-items: center;
  gap: var(--workspace-space-2);
}

.developer-grid {
  display: grid;
  grid-template-columns: minmax(290px, 0.72fr) minmax(0, 1.28fr);
  gap: var(--workspace-space-4);
}
.developer-grid > :first-child { grid-row: 1 / span 2; align-self: start; }
.usage-panel { grid-column: 2; }

.application-tools {
  padding: var(--workspace-space-3);
  border-bottom: 1px solid var(--workspace-border);
}

.key-list {
  display: grid;
}

.application-table + :deep(.ant-pagination),
.usage-list > :deep(.ant-pagination) {
  margin: 0;
  padding: 12px 16px;
}

.application-row {
  display: flex;
  width: 100%;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  gap: var(--workspace-space-3);
  padding: 12px 16px;
  border: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.application-table :deep(.ant-table-tbody > tr > td) {
  padding: 0;
}

.application-row:hover,
.application-row.is-active {
  background: var(--workspace-main-soft);
}

.application-row__main {
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
.key-row small {
  color: var(--workspace-text-muted);
  font-size: 11px;
}

.key-row {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto 32px;
  align-items: center;
  gap: var(--workspace-space-4);
  padding: 16px 18px;
  border-bottom: 1px solid var(--workspace-border);
}

.key-row__identity {
  display: grid;
  min-width: 0;
  gap: 5px;
}

.key-row code,
.issued-key code {
  overflow: hidden;
  color: var(--workspace-text-secondary);
  font-family: var(--workspace-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.key-row__quota {
  color: var(--workspace-text-secondary);
  font-size: 11px;
}

.stale-notice {
  padding: 8px 14px;
  color: var(--workspace-warning);
  background: var(--workspace-warning-soft);
  font-size: 12px;
}

.quota-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--workspace-space-3);
}

.issued-key {
  display: flex;
  align-items: center;
  gap: var(--workspace-space-3);
  margin-top: var(--workspace-space-4);
  padding: 14px;
  border: 1px solid var(--workspace-border-strong);
  border-radius: var(--workspace-radius-sm);
  background: var(--workspace-surface-muted);
}

.issued-key code {
  flex: 1;
  color: var(--workspace-text);
}

@media (max-width: 900px) {
  .developer-grid {
    grid-template-columns: 1fr;
  }
  .developer-grid > :first-child,
  .usage-panel { grid-column: auto; grid-row: auto; }
}

@media (max-width: 560px) {
  .developer-header {
    align-items: flex-start;
  }

  .developer-header__actions .button-label {
    display: none;
  }

  .key-panel :deep(.workspace-panel__header) {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .key-panel :deep(.workspace-panel__header > div:first-child) {
    width: 100%;
    min-width: 0;
  }

  .key-panel :deep(.workspace-panel__title),
  .key-panel :deep(.workspace-panel__subtitle) {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .key-toolbar {
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .key-row {
    grid-template-columns: minmax(0, 1fr) 32px;
  }

  .secondary-key-action {
    width: 32px;
    padding: 0;
  }

  .secondary-key-action .button-label {
    display: none;
  }

  .key-row__quota {
    display: none;
  }

  .quota-grid {
    grid-template-columns: 1fr;
  }
}
</style>
