import { monthlyPointsLimit } from '../../billing/presentation.ts'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import type { FormInstance } from 'ant-design-vue'
import { DeleteOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import {
  changeApplicationStatus,
  createApplication,
  createDeveloperKey,
  deleteApplication,
  getApplication,
  listApplications,
  listApplicationUsage,
  listDeveloperKeys,
  revokeDeveloperKey,
  rotateDeveloperKey,
  updateApplication
} from '../api/index.ts'
import type { Application, ApplicationUsage, DeveloperKey, IssuedKey } from '../api/index.ts'

type Tab = 'overview' | 'keys' | 'models' | 'usage'
const tabValue = (value: unknown): Tab =>
  value === 'keys' || value === 'models' || value === 'usage' ? value : 'overview'
const defaultKeyForm = () => ({
  keyName: '',
  monthlyPointsLimit: '',
  modelIds: [] as string[],
  expiresAt: null as string | null,
  rpmLimit: null as number | null,
  tpmLimit: null as number | null,
  concurrencyLimit: null as number | null
})
function confirm(text: string): Promise<boolean> {
  return new Promise((resolve) =>
    Modal.confirm({
      title: text,
      okText: '确定',
      cancelText: '取消',
      onOk: () => {
        resolve(true)
      },
      onCancel: () => {
        resolve(false)
      }
    })
  )
}

export function useApplications() {
  const route = useRoute(),
    router = useRouter()
  const scopes = {
    list: new RequestScope(),
    detail: new RequestScope(),
    keys: new RequestScope(),
    models: new RequestScope(),
    usage: new RequestScope(),
    mutation: new RequestScope()
  }
  const applications = ref<Application[]>([]),
    selectedApplication = ref<Application | null>(null)
  const keys = ref<DeveloperKey[]>([]),
    models = ref<ChatModel[]>([]),
    usageRows = ref<ApplicationUsage[]>([])
  const total = ref(0),
    usageTotal = ref(0)
  const loading = ref(false),
    detailLoading = ref(false),
    keyLoading = ref(false),
    modelLoading = ref(false),
    usageLoading = ref(false),
    saving = ref(false)
  const error = ref(''),
    keyError = ref(''),
    modelError = ref(''),
    usageError = ref(''),
    formError = ref('')
  const errorDetails = ref<unknown>()
  const query = reactive({ applicationName: '', pageNum: 1, pageSize: 10 }),
    usageQuery = reactive({ pageNum: 1, pageSize: 10 })
  const applicationFormOpen = ref(false),
    keyFormOpen = ref(false),
    issuedKeyOpen = ref(false)
  const applicationFormRef = ref<FormInstance>(),
    keyFormRef = ref<FormInstance>()
  const applicationForm = reactive({ applicationCode: '', applicationName: '' }),
    keyForm = reactive(defaultKeyForm())
  const editingApplication = ref<Application | null>(null),
    issuedKey = ref<IssuedKey | null>(null),
    pendingIssuedKey = ref<IssuedKey | null>(null)
  const appId = computed(() =>
    typeof route.query.applicationId === 'string' ? route.query.applicationId : ''
  )
  const showApplicationList = computed(() => appId.value === ''),
    showDocs = computed(() => route.query.view === 'docs'),
    activeTab = computed(() => tabValue(route.query.tab))
  const canListKeys = true,
    canCreateKey = true,
    canViewModelAuthorizations = true,
    canQueryUsage = true
  const keyActions: MenuAction[] = [
    { command: 'rotate', label: '轮换密钥', icon: ReloadOutlined },
    { command: 'revoke', label: '撤销密钥', icon: DeleteOutlined, danger: true, divided: true }
  ]
  const modelOptions = computed(() =>
    models.value
      .filter((model) => model.enabled && model.published)
      .map((model) => ({ label: model.name, value: model.id }))
  )
  const applicationColumns = [{ title: '应用', key: 'application' }]
  const applicationRules = {
    applicationCode: [
      { required: true, message: '应用编码不能为空' },
      {
        pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/,
        message: '仅支持字母、数字、点、下划线和短横线'
      }
    ],
    applicationName: [{ required: true, whitespace: true, message: '应用名称不能为空' }]
  }
  const keyRules = {
    keyName: [{ required: true, whitespace: true, message: '密钥名称不能为空' }],
    modelIds: [{ type: 'array' as const, required: true, min: 1, message: '至少选择一个模型' }]
  }
  function clearIssuedKey(): void {
    issuedKeyOpen.value = false
    issuedKey.value = null
    pendingIssuedKey.value = null
  }
  function closeForms(): void {
    scopes.mutation.replace()
    saving.value = false
    applicationFormOpen.value = false
    keyFormOpen.value = false
    formError.value = ''
    Object.assign(keyForm, defaultKeyForm())
    clearIssuedKey()
  }
  function clearResources(): void {
    scopes.keys.replace()
    scopes.models.replace()
    scopes.usage.replace()
    keys.value = []
    models.value = []
    usageRows.value = []
    keyLoading.value = false
    modelLoading.value = false
    usageLoading.value = false
    keyError.value = ''
    modelError.value = ''
    usageError.value = ''
    usageQuery.pageNum = 1
  }
  async function loadApplications(): Promise<Application[] | null> {
    const context = scopes.list.replace()
    loading.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      const result = await listApplications(
        { page: query.pageNum, page_size: query.pageSize, search: query.applicationName },
        context.signal
      )
      if (context.isCurrent()) {
        applications.value = result.rows
        total.value = result.total
        return result.rows
      }
      return null
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
      return null
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
  async function loadKeys(): Promise<void> {
    const id = selectedApplication.value?.applicationId
    if (!id) return
    const context = scopes.keys.replace()
    keyLoading.value = true
    keyError.value = ''
    try {
      const result = await listDeveloperKeys(id, context.signal)
      if (context.isCurrent() && selectedApplication.value?.applicationId === id)
        keys.value = result
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) keyError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) keyLoading.value = false
    }
  }
  async function loadModels(): Promise<void> {
    const context = scopes.models.replace()
    modelLoading.value = true
    modelError.value = ''
    try {
      const result = await getModels(context.signal)
      if (context.isCurrent()) models.value = result
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) modelError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) modelLoading.value = false
    }
  }
  async function loadModelAuthorizations(): Promise<void> {
    await Promise.all([loadKeys(), loadModels()])
  }
  async function loadUsage(): Promise<void> {
    const id = selectedApplication.value?.applicationId
    if (!id) return
    const context = scopes.usage.replace()
    usageLoading.value = true
    usageError.value = ''
    try {
      const result = await listApplicationUsage(
        id,
        { page: usageQuery.pageNum, page_size: usageQuery.pageSize },
        context.signal
      )
      if (context.isCurrent() && selectedApplication.value?.applicationId === id) {
        usageRows.value = result.rows
        usageTotal.value = result.total
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) usageError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) usageLoading.value = false
    }
  }
  async function loadActiveTabData(): Promise<void> {
    if (showDocs.value || !selectedApplication.value) return
    if (activeTab.value === 'keys') await loadKeys()
    else if (activeTab.value === 'models') await loadModelAuthorizations()
    else if (activeTab.value === 'usage') await loadUsage()
  }
  async function loadDetail(id: string): Promise<void> {
    const context = scopes.detail.replace()
    detailLoading.value = true
    error.value = ''
    errorDetails.value = undefined
    clearResources()
    selectedApplication.value = null
    try {
      const result = await getApplication(id, context.signal)
      if (!context.isCurrent() || appId.value !== id) return
      selectedApplication.value = result
      detailLoading.value = false
      await loadActiveTabData()
    } catch (reason) {
      if (!context.isCurrent() || isAborted(reason)) return
      if (reason instanceof ApiError && reason.status === 404) {
        const rows = await loadApplications()
        if (!context.isCurrent() || rows === null) return
        const first = rows[0]
        if (first) await selectApplication(first)
        else await backToApplicationList()
      } else error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) detailLoading.value = false
    }
  }
  watch(
    () => [appId.value, activeTab.value, showDocs.value],
    ([id, tab, docs], previous) => {
      errorDetails.value = undefined
      if (previous && (id !== previous[0] || tab !== previous[1])) closeForms()
      if (docs) {
        closeForms()
        scopes.detail.replace()
        detailLoading.value = false
        clearResources()
        return
      }
      if (typeof id === 'string' && id) {
        if (selectedApplication.value?.applicationId !== id || previous?.[2]) void loadDetail(id)
        else void loadActiveTabData()
      } else {
        scopes.detail.replace()
        detailLoading.value = false
        clearResources()
        selectedApplication.value = null
      }
    },
    { immediate: true }
  )
  onMounted(() => {
    void loadApplications()
  })
  onBeforeUnmount(() => {
    Object.values(scopes).forEach((scope) => scope.dispose())
    issuedKey.value = null
    pendingIssuedKey.value = null
    Object.assign(keyForm, defaultKeyForm())
  })
  async function selectApplication(application: Application): Promise<void> {
    await router.replace({
      path: route.path,
      query: { ...route.query, applicationId: application.applicationId, tab: 'overview' }
    })
  }
  async function backToApplicationList(): Promise<void> {
    await router.replace({
      path: route.path,
      query: { ...route.query, applicationId: undefined, tab: undefined }
    })
    await loadApplications()
  }
  async function changeTab(tab: unknown): Promise<void> {
    await router.replace({ path: route.path, query: { ...route.query, tab: tabValue(tab) } })
  }
  async function refreshCurrentView(): Promise<void> {
    await loadApplications()
    if (appId.value) await loadDetail(appId.value)
  }
  async function changeUsagePage(page: number, size: number): Promise<void> {
    usageQuery.pageNum = page
    usageQuery.pageSize = size
    await loadUsage()
  }
  function openApplicationForm(application: Application | null = null): void {
    scopes.mutation.replace()
    editingApplication.value = application
    applicationForm.applicationCode = application?.applicationCode ?? ''
    applicationForm.applicationName = application?.applicationName ?? ''
    formError.value = ''
    applicationFormOpen.value = true
  }
  function closeApplicationForm(): void {
    scopes.mutation.replace()
    saving.value = false
    applicationFormOpen.value = false
    formError.value = ''
  }
  async function saveApplication(): Promise<void> {
    if (
      saving.value ||
      !(await applicationFormRef.value
        ?.validate()
        .then(() => true)
        .catch(() => false))
    )
      return
    const context = scopes.mutation.replace()
    const editing = editingApplication.value
    saving.value = true
    formError.value = ''
    try {
      const result = editing
        ? await updateApplication(
            editing.applicationId,
            { name: applicationForm.applicationName.trim(), expected_version: editing.version },
            context.signal
          )
        : await createApplication(
            {
              code: applicationForm.applicationCode.trim(),
              name: applicationForm.applicationName.trim()
            },
            context.signal
          )
      if (!context.isCurrent()) return
      applicationFormOpen.value = false
      if (editing && appId.value === result.applicationId) {
        scopes.detail.replace()
        detailLoading.value = false
        selectedApplication.value = result
      }
      await loadApplications()
      if (!context.isCurrent()) return
      if (!editing) await selectApplication(result)
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) formError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  async function applicationAction(action: 'enable' | 'disable' | 'delete'): Promise<void> {
    const application = selectedApplication.value
    if (!application || saving.value) return
    const context = scopes.mutation.replace()
    const labels = {
      enable: '重新启用后，该应用下仍然有效的访问密钥可继续调用。是否继续？',
      disable: '停用后，该应用下所有访问密钥将无法调用。是否继续？',
      delete:
        '删除后应用将不再显示，所有有效访问密钥将立即停用，历史审计与调用记录仍会保留。是否继续？'
    }
    if (!(await confirm(labels[action])) || !context.isCurrent()) return
    saving.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      if (action === 'delete') {
        await deleteApplication(application.applicationId, context.signal)
        if (context.isCurrent()) await backToApplicationList()
      } else {
        const result = await changeApplicationStatus(
          application.applicationId,
          action,
          context.signal
        )
        if (context.isCurrent()) {
          selectedApplication.value = result
          await loadApplications()
        }
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        errorDetails.value = reason instanceof ApiError ? reason.details : undefined
      }
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  async function openKeyForm(): Promise<void> {
    if (selectedApplication.value?.status !== 'active') return
    formError.value = ''
    Object.assign(keyForm, defaultKeyForm())
    keyFormOpen.value = true
    await loadModels()
  }
  function closeKeyForm(): void {
    scopes.mutation.replace()
    saving.value = false
    keyFormOpen.value = false
    pendingIssuedKey.value = null
    Object.assign(keyForm, defaultKeyForm())
    formError.value = ''
  }
  function handleKeyFormClosed(): void {
    Object.assign(keyForm, defaultKeyForm())
    const value = pendingIssuedKey.value
    pendingIssuedKey.value = null
    if (value && value.key.applicationId === selectedApplication.value?.applicationId) {
      issuedKey.value = value
      issuedKeyOpen.value = true
    }
  }
  async function saveKey(): Promise<void> {
    const id = selectedApplication.value?.applicationId
    if (
      !id ||
      saving.value ||
      !(await keyFormRef.value
        ?.validate()
        .then(() => true)
        .catch(() => false))
    )
      return
    let pointLimit: string | null
    try {
      pointLimit = monthlyPointsLimit(keyForm.monthlyPointsLimit)
    } catch (reason) {
      formError.value = errorMessage(reason)
      return
    }
    const context = scopes.mutation.replace()
    saving.value = true
    formError.value = ''
    try {
      const result = await createDeveloperKey(
        id,
        {
          name: keyForm.keyName.trim(),
          monthly_points_limit: pointLimit,
          model_ids: [...keyForm.modelIds],
          rpm_limit: keyForm.rpmLimit,
          tpm_limit: keyForm.tpmLimit,
          concurrency_limit: keyForm.concurrencyLimit,
          expires_at: keyForm.expiresAt ? new Date(keyForm.expiresAt).toISOString() : null
        },
        context.signal
      )
      if (!context.isCurrent() || selectedApplication.value?.applicationId !== id) return
      pendingIssuedKey.value = result
      keyFormOpen.value = false
      await loadKeys()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) formError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  async function handleKeyAction(command: string, item: DeveloperKey): Promise<void> {
    const id = selectedApplication.value?.applicationId
    if (!id || saving.value || (command !== 'rotate' && command !== 'revoke')) return
    const context = scopes.mutation.replace()
    if (
      !(await confirm(
        command === 'rotate'
          ? '轮换后旧密钥将保留 24 小时宽限期，是否继续？'
          : '撤销后该密钥立即失效，是否继续？'
      )) ||
      !context.isCurrent()
    )
      return
    saving.value = true
    try {
      if (command === 'rotate') {
        const result = await rotateDeveloperKey(id, item.keyId, context.signal)
        if (context.isCurrent()) {
          issuedKey.value = result
          issuedKeyOpen.value = true
        }
      } else await revokeDeveloperKey(id, item.keyId, context.signal)
      if (context.isCurrent()) await loadKeys()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) keyError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  async function copyIssuedKey(): Promise<void> {
    const value = issuedKey.value?.apiKey
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      void message.success('访问密钥已复制')
    } catch {
      void message.error('复制失败，请手动复制')
    }
  }
  async function openDocs(): Promise<void> {
    await router.replace({ path: route.path, query: { ...route.query, view: 'docs' } })
  }
  async function closeDocs(): Promise<void> {
    await router.replace({ path: route.path, query: { ...route.query, view: undefined }, hash: '' })
  }
  function setModelIds(value: unknown): void {
    keyForm.modelIds =
      Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : []
  }
  function setLimit(field: 'rpmLimit' | 'tpmLimit' | 'concurrencyLimit', value: unknown): void {
    keyForm[field] = typeof value === 'number' && Number.isFinite(value) ? value : null
  }
  function setExpiry(value: unknown): void {
    keyForm.expiresAt = typeof value === 'string' && value ? value : null
  }
  return {
    detailLoading,
    setModelIds,
    setLimit,
    setExpiry,
    applications,
    selectedApplication,
    keys,
    models,
    usageRows,
    total,
    usageTotal,
    loading,
    keyLoading,
    modelLoading,
    usageLoading,
    saving,
    error,
    errorDetails,
    keyError,
    modelError,
    usageError,
    formError,
    query,
    usageQuery,
    applicationFormOpen,
    keyFormOpen,
    issuedKeyOpen,
    applicationFormRef,
    keyFormRef,
    applicationForm,
    keyForm,
    editingApplication,
    issuedKey,
    showApplicationList,
    showDocs,
    activeTab,
    canListKeys,
    canCreateKey,
    canViewModelAuthorizations,
    canQueryUsage,
    keyActions,
    modelOptions,
    applicationColumns,
    applicationRules,
    keyRules,
    loadApplications,
    loadKeys,
    loadModels,
    loadModelAuthorizations,
    loadUsage,
    selectApplication,
    backToApplicationList,
    changeTab,
    refreshCurrentView,
    changeUsagePage,
    openApplicationForm,
    closeApplicationForm,
    saveApplication,
    disableSelectedApplication: () => applicationAction('disable'),
    enableSelectedApplication: () => applicationAction('enable'),
    deleteSelectedApplication: () => applicationAction('delete'),
    openKeyForm,
    closeKeyForm,
    handleKeyFormClosed,
    saveKey,
    handleKeyAction,
    clearIssuedKey,
    copyIssuedKey,
    openDocs,
    closeDocs
  }
}
