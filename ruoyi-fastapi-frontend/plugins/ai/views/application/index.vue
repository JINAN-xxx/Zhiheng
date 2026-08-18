<template>
  <main class="app-container developer-api-page">
    <header class="developer-header">
      <div>
        <span class="developer-kicker">DEVELOPER ACCESS</span>
        <h2>Developer API</h2>
        <p>管理应用、模型授权与平台 API Key。</p>
      </div>
      <div class="developer-header__actions">
        <el-tooltip content="刷新应用" placement="bottom">
          <el-button circle :icon="Refresh" aria-label="刷新应用" @click="loadApplications" />
        </el-tooltip>
        <el-button v-if="can('ai:application:add')" type="primary" :icon="Plus" @click="openApplicationForm()">新建应用</el-button>
      </div>
    </header>

    <section class="developer-grid">
      <WorkspacePanel title="应用" subtitle="每个应用拥有独立 Key 与模型权限" flush>
        <div class="application-tools">
          <el-input v-model="query.applicationName" clearable :prefix-icon="Search" placeholder="搜索应用" @keyup.enter="loadApplications" />
        </div>
        <WorkspaceState v-if="loading && !applications.length" kind="loading" />
        <WorkspaceState v-else-if="error && !applications.length" kind="error" :message="error" @retry="loadApplications" />
        <WorkspaceState v-else-if="!applications.length" kind="empty" title="暂无开发者应用" />
        <template v-else>
          <div v-if="error" class="stale-notice">刷新失败，当前列表可能已过期。</div>
          <div class="application-list">
            <button
              v-for="application in applications"
              :key="application.applicationId"
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
          </div>
          <pagination
            v-show="total > query.pageSize"
            v-model:page="query.pageNum"
            v-model:limit="query.pageSize"
            :total="total"
            @pagination="loadApplications"
          />
        </template>
      </WorkspacePanel>

      <WorkspacePanel
        class="key-panel"
        :title="selectedApplication?.applicationName || 'API Key'"
        :subtitle="selectedApplication ? selectedApplication.applicationCode : '先选择一个应用'"
        flush
      >
        <template #header>
          <div v-if="selectedApplication" class="key-toolbar">
            <el-button v-if="can('ai:application:edit')" class="secondary-key-action" text :icon="Edit" aria-label="编辑应用" title="编辑应用" @click="openApplicationForm(selectedApplication)">编辑</el-button>
            <el-button
              v-if="can('ai:application:disable') && selectedApplication.status === 'active'"
              text
              type="danger"
              :icon="CircleClose"
              class="secondary-key-action"
              aria-label="停用应用"
              title="停用应用"
              @click="disableSelectedApplication"
            >停用</el-button>
            <el-button
              v-if="canCreateKey && selectedApplication.status === 'active'"
              type="primary"
              :icon="Key"
              @click="openKeyForm"
            >创建 Key</el-button>
          </div>
        </template>

        <WorkspaceState v-if="!selectedApplication" kind="empty" title="选择应用以查看 API Key" />
        <WorkspaceState v-else-if="!canListKeys" kind="forbidden" title="无权查看 API Key" message="当前账号缺少 API Key 列表权限。" />
        <WorkspaceState v-else-if="keyLoading && !keys.length" kind="loading" />
        <WorkspaceState v-else-if="keyError && !keys.length" kind="error" :message="keyError" @retry="loadKeys" />
        <WorkspaceState v-else-if="!keys.length" kind="empty" title="暂无 API Key" message="为当前应用创建第一个 Key。" />
        <div v-else class="key-list">
          <div v-if="keyError" class="stale-notice">刷新失败，当前 Key 列表可能已过期。</div>
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
              <span>RPM {{ item.quota.rpmLimit ?? '不限' }}</span>
              <span>TPM {{ item.quota.tpmLimit ?? '不限' }}</span>
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
    </section>

    <el-dialog v-model="applicationFormOpen" :title="editingApplication ? '编辑应用' : '新建应用'" width="500px" destroy-on-close>
      <el-form ref="applicationFormRef" :model="applicationForm" :rules="applicationRules" label-position="top">
        <el-form-item v-if="!editingApplication" label="应用编码" prop="applicationCode">
          <el-input v-model="applicationForm.applicationCode" maxlength="100" placeholder="例如 internal-tools" />
        </el-form-item>
        <el-form-item label="应用名称" prop="applicationName">
          <el-input v-model="applicationForm.applicationName" maxlength="100" placeholder="例如内部智能工具" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="applicationFormOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveApplication">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="keyFormOpen" title="创建 API Key" width="600px" destroy-on-close @closed="handleKeyFormClosed">
      <el-form ref="keyFormRef" :model="keyForm" :rules="keyRules" label-position="top">
        <el-form-item label="Key 名称" prop="keyName"><el-input v-model="keyForm.keyName" maxlength="100" /></el-form-item>
        <el-form-item label="授权模型" prop="modelIds">
          <el-select v-model="keyForm.modelIds" multiple filterable style="width: 100%" placeholder="至少选择一个已发布模型">
            <el-option v-for="model in models" :key="model.modelId" :label="model.title || model.modelCode" :value="model.modelId" />
          </el-select>
        </el-form-item>
        <div class="quota-grid">
          <el-form-item label="RPM"><el-input-number v-model="keyForm.rpmLimit" :min="1" controls-position="right" /></el-form-item>
          <el-form-item label="TPM"><el-input-number v-model="keyForm.tpmLimit" :min="1" controls-position="right" /></el-form-item>
          <el-form-item label="并发数"><el-input-number v-model="keyForm.concurrencyLimit" :min="1" controls-position="right" /></el-form-item>
        </div>
        <el-form-item label="过期时间"><el-date-picker v-model="keyForm.expiresAt" type="datetime" style="width: 100%" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="keyFormOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveKey">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="issuedKeyOpen" title="API Key 已生成" width="560px" :close-on-click-modal="false" @close="clearIssuedKey">
      <el-alert type="warning" :closable="false" title="该 Key 仅展示一次，关闭后无法再次查看。" />
      <div class="issued-key">
        <code>{{ issuedKey?.apiKey }}</code>
        <el-button :icon="CopyDocument" aria-label="复制 API Key" title="复制 API Key" @click="copyIssuedKey" />
      </div>
      <template #footer><el-button type="primary" @click="issuedKeyOpen = false">我已保存</el-button></template>
    </el-dialog>
  </main>
</template>

<script setup name="AiApplication">
import { CircleClose, CopyDocument, Delete, Edit, Key, Plus, Refresh, Search } from '@element-plus/icons-vue'
import useUserStore from '@/store/modules/user'
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

const { proxy } = getCurrentInstance()
const userStore = useUserStore()
const can = (permission) => hasPermission(userStore.permissions || [], permission)
const canListKeys = computed(() => can('ai:application:key:list'))
const canCreateKey = computed(() => can('ai:application:key:add') && can('ai:model:list'))
const query = reactive({ applicationName: '', pageNum: 1, pageSize: 10 })
const applications = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const selectedApplication = ref(null)
const keys = ref([])
const keyLoading = ref(false)
const keyError = ref('')
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
  keyName: [{ required: true, message: 'Key 名称不能为空', trigger: 'blur' }],
  modelIds: [{ type: 'array', required: true, min: 1, message: '至少选择一个模型', trigger: 'change' }],
}
const keyActions = computed(() => [
  can('ai:application:key:rotate') && { command: 'rotate', label: '轮换 Key', icon: Refresh },
  can('ai:application:key:revoke') && { command: 'revoke', label: '撤销 Key', icon: Delete, divided: true },
].filter(Boolean))

function defaultKeyForm() {
  return { keyName: '', modelIds: [], expiresAt: null, rpmLimit: null, tpmLimit: null, concurrencyLimit: null }
}

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '从未'
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
  selectedApplication.value = application
  if (application && canListKeys.value) await loadKeys()
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
    if (requestId === keyRequest) keyError.value = reason?.message || 'API Key 加载失败'
  } finally {
    if (requestId === keyRequest) keyLoading.value = false
  }
}

function openApplicationForm(application = null) {
  editingApplication.value = application
  applicationForm.applicationCode = application?.applicationCode || ''
  applicationForm.applicationName = application?.applicationName || ''
  applicationFormOpen.value = true
}

async function saveApplication() {
  if (!await applicationFormRef.value.validate().then(() => true).catch(() => false)) return
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
  await proxy.$modal.confirm('停用后，该应用下所有 Key 将无法调用。是否继续？')
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
  if (!await keyFormRef.value.validate().then(() => true).catch(() => false)) return
  saving.value = true
  try {
    const response = await createDeveloperKey(selectedApplication.value.applicationId, buildDeveloperKeyPayload(keyForm))
    pendingIssuedKey.value = extractIssuedKey(response)
    keyFormOpen.value = false
    await loadKeys()
  } finally {
    saving.value = false
  }
}

async function handleKeyAction(command, item) {
  if (command === 'rotate') {
    await proxy.$modal.confirm('轮换后旧 Key 将保留 24 小时宽限期，是否继续？')
    const response = await rotateDeveloperKey(selectedApplication.value.applicationId, item.keyId, { gracePeriodSeconds: 86400 })
    showIssuedKey(response)
    await loadKeys()
  } else if (command === 'revoke') {
    await proxy.$modal.confirm('撤销后该 Key 立即失效，是否继续？')
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
  proxy.$modal.msgSuccess('API Key 已复制')
}

onMounted(loadApplications)
onBeforeUnmount(() => { applicationRequest += 1; keyRequest += 1; pendingIssuedKey.value = null; clearIssuedKey() })
</script>

<style scoped lang="scss">
.developer-api-page {
  color: var(--workspace-text);
  font-family: var(--workspace-font-family);
}

.developer-header {
  display: flex;
  min-height: 72px;
  align-items: center;
  justify-content: space-between;
  gap: var(--workspace-space-4);
  margin-bottom: var(--workspace-space-4);
}

.developer-kicker {
  color: var(--workspace-main-deep);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.developer-header h2 {
  margin: 4px 0 2px;
  font-size: 21px;
}

.developer-header p {
  margin: 0;
  color: var(--workspace-text-secondary);
  font-size: 12px;
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

.application-tools {
  padding: var(--workspace-space-3);
  border-bottom: 1px solid var(--workspace-border);
}

.application-list,
.key-list {
  display: grid;
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
  border-bottom: 1px solid var(--workspace-border);
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
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
}

@media (max-width: 560px) {
  .developer-api-page {
    padding: 12px;
  }

  .developer-header {
    align-items: flex-start;
  }

  .developer-header__actions .el-button span {
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

  .secondary-key-action :deep(span) {
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
