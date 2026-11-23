import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Modal } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { useWindowSize } from '@vueuse/core'
import { copyText } from '../../../shared/clipboard.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  getConnections,
  getConnectionOptions,
  getModels,
  getTrialModels,
  controlConnection,
  controlModel
} from '../api/index.ts'
import type { ChatModel, ModelConnection, ConnectionOption } from '../api/index.ts'
import { modelConnectionIssue } from '../presentation.ts'
import type { ModelControlAction } from '../presentation.ts'
import type { ModelAccess } from './useModelAccess.ts'

interface ModelCatalogOptions {
  busy: () => boolean
  deleted: (id: string) => void
}
export function useModelCatalog(
  credentialPage: boolean,
  access: ModelAccess,
  options: ModelCatalogOptions
) {
  const {
    canCreateModels,
    canUpdateModels,
    canDeleteModels,
    canToggleModels,
    canCreateCredentials,
    canUpdateCredentials,
    canDeleteCredentials,
    canToggleCredentials,
    canReadModels,
    canReadCredentials,
    canTestModels,
    canReadRates,
    hasModelActions,
    denyTrialAccess
  } = access
  const modelSearch = ref('')
  const modelType = ref('all')
  const modelFilters = [
    { value: 'all', label: '全部模型' },
    { value: 'chat', label: '对话模型' },
    { value: 'embedding', label: '向量模型' },
    { value: 'rerank', label: '重排序模型' }
  ]
  const { width } = useWindowSize()
  const isMobile = computed(() => width.value <= 760)
  const lastSuccessAt = ref('')
  const modelColumns = [
    { title: '模型', key: 'model', width: 240 },
    { title: '类型', key: 'type', width: 100 },
    { title: '连接状态', key: 'binding', width: 120 },
    { title: '启用状态', key: 'status', width: 100 },
    { title: '输出上限', key: 'output', width: 110 },
    { title: '操作', key: 'actions', width: 240 }
  ]
  const visibleModelColumns = computed<TableColumnsType<ChatModel>>(() =>
    isMobile.value
      ? [
          { title: '模型代码', key: 'model' },
          ...(hasModelActions.value
            ? [{ title: '操作', key: 'actions', width: 64, align: 'right' as const }]
            : [])
        ]
      : modelColumns.filter(
          (column) =>
            (column.key !== 'binding' || canReadModels.value) &&
            (column.key !== 'actions' || hasModelActions.value)
        )
  )
  const connections = ref<ModelConnection[]>([])
  const connectionChoices = ref<ConnectionOption[]>([])
  const connectionData = computed(() =>
    credentialPage ? connections.value : connectionChoices.value
  )
  const models = ref<ChatModel[]>([])
  const filteredModels = computed(() => {
    const search = modelSearch.value.trim().toLowerCase()
    return models.value.filter(
      (model) =>
        (modelType.value === 'all' || model.model_type === modelType.value) &&
        [model.name, model.id, model.upstream_model, connectionName(model.connection_id)]
          .join(' ')
          .toLowerCase()
          .includes(search)
    )
  })
  function connectionName(id: string): string {
    return connectionData.value.find((connection) => connection.id === id)?.name ?? '连接不可用'
  }
  function connectionEnabled(id: string): boolean {
    return connectionData.value.some((connection) => connection.id === id && connection.enabled)
  }
  function connectionDisabled(id: string): boolean {
    return connectionData.value.find((connection) => connection.id === id)?.enabled === false
  }
  const loading = ref(false)
  const controllingConnection = ref(false)
  const error = ref('')
  const errorDetails = ref<unknown>()
  const detailModelId = ref('')
  const detailModel = computed(
    () => models.value.find((model) => model.id === detailModelId.value) ?? null
  )
  const scope = new RequestScope()
  const connectionControlScope = new RequestScope()
  const modelScopes = new Map<string, RequestScope>()
  const modelPending = reactive<Record<string, boolean>>({})
  const modelFailures = reactive<
    Record<string, { message: string; details?: unknown } | undefined>
  >({})
  const confirmationOpen = ref(false)
  let closeConfirmation: (() => void) | undefined
  let copyEpoch = 0
  watch(
    detailModel,
    () => {
      copyEpoch += 1
    },
    { flush: 'sync' }
  )
  const connectionOptions = computed(() =>
    connectionData.value
      .filter((item) => item.enabled)
      .map((item) => ({ value: item.id, label: item.name }))
  )
  const busy = () => options.busy() || controllingConnection.value
  onBeforeUnmount(() => {
    scope.dispose()
    connectionControlScope.dispose()
    for (const item of modelScopes.values()) item.dispose()
    modelScopes.clear()
    closeConfirmation?.()
  })
  async function load(): Promise<void> {
    const context = scope.replace()
    loading.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      const [items, availableModels, options] = await Promise.all([
        credentialPage && canReadCredentials.value
          ? getConnections(context.signal)
          : Promise.resolve([]),
        credentialPage
          ? Promise.resolve([])
          : canReadModels.value
            ? getModels(context.signal, true)
            : canTestModels.value
              ? getTrialModels(context.signal)
              : getModels(context.signal),
        canReadModels.value && !credentialPage
          ? getConnectionOptions(context.signal)
          : Promise.resolve([])
      ])
      if (context.isCurrent()) {
        connections.value = items
        connectionChoices.value = options
        models.value = availableModels
        lastSuccessAt.value = new Date().toLocaleString('zh-CN', { hour12: false })
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        if (reason instanceof ApiError && reason.status === 403 && reason.code === 'forbidden') {
          connections.value = []
          models.value = []
          closeDetails()
          if (!canReadModels.value && canTestModels.value) denyTrialAccess()
        }
      }
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
  onMounted(() => {
    void load()
  })
  async function copyModelCode(value: string): Promise<void> {
    if (!value.trim()) return
    const context = scope.current()
    const epoch = ++copyEpoch
    try {
      await copyText(value)
    } catch {
      if (!context.isCurrent() || epoch !== copyEpoch) return
      error.value = '复制失败，请手动复制模型代码'
      errorDetails.value = undefined
    }
  }
  function confirmOperation(title: string, destructive = false): Promise<boolean> {
    confirmationOpen.value = true
    return new Promise((resolve) => {
      const finish = (accepted: boolean) => {
        confirmationOpen.value = false
        closeConfirmation = undefined
        resolve(accepted)
      }
      const dialog = Modal.confirm({
        title,
        okText: destructive ? '删除模型' : '确定',
        okButtonProps: { danger: destructive },
        cancelText: '取消',
        onOk: () => finish(true),
        onCancel: () => finish(false)
      })
      closeConfirmation = () => {
        dialog.destroy()
        finish(false)
      }
    })
  }

  async function changeModel(model: ChatModel, action: ModelControlAction): Promise<void> {
    const id = model.id
    if (
      busy() ||
      confirmationOpen.value ||
      modelPending[id] ||
      !(action === 'delete' ? canDeleteModels.value : canToggleModels.value)
    )
      return
    const operation = modelScopes.get(id) ?? new RequestScope()
    modelScopes.set(id, operation)
    const context = operation.replace()
    modelPending[id] = true
    modelFailures[id] = undefined
    try {
      if (action === 'delete' || action === 'disable') {
        const accepted = await confirmOperation(
          action === 'delete'
            ? '删除该模型？模型将自动停用并从目录移除，历史调用与账单记录仍会保留。当前依赖或在途使用需要先处理。'
            : '调整后，新请求将不能使用该模型。是否继续？'
        )
        if (!accepted) return
      }
      if (
        !context.isCurrent() ||
        !(action === 'delete' ? canDeleteModels.value : canToggleModels.value)
      )
        return
      error.value = ''
      errorDetails.value = undefined
      let updated: ChatModel | undefined
      try {
        updated = await controlModel(id, action, context.signal)
      } catch (reason) {
        if (
          action !== 'delete' ||
          !context.isCurrent() ||
          !access.authority.value?.is_admin ||
          !(reason instanceof ApiError) ||
          reason.code !== 'model_referenced' ||
          !reason.details ||
          typeof reason.details !== 'object' ||
          !('retirable_key_count' in reason.details)
        )
          throw reason
        const count = reason.details.retirable_key_count
        if (typeof count !== 'number' || !Number.isSafeInteger(count) || count <= 0) throw reason
        const accepted = await confirmOperation(
          `删除「${model.name}」？${count} 把 API 密钥将无法调用此模型，其他授权和历史记录仍会保留。`,
          true
        )
        if (!accepted) throw reason
        if (!context.isCurrent() || !canDeleteModels.value || !access.authority.value?.is_admin)
          return
        await controlModel(id, 'delete', context.signal, true)
      }
      if (!context.isCurrent()) return
      const interrupted = loading.value
      scope.replace()
      loading.value = false
      if (action === 'delete') {
        models.value = models.value.filter((item) => item.id !== id)
        if (detailModelId.value === id) closeDetails()
        options.deleted(id)
      } else if (updated)
        models.value = models.value.map((item) => (item.id === id ? updated : item))
      if (interrupted) void load()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        modelFailures[id] = {
          message: errorMessage(reason),
          details: reason instanceof ApiError ? reason.details : undefined
        }
        if (detailModelId.value !== id) {
          error.value = errorMessage(reason)
          errorDetails.value = reason instanceof ApiError ? reason.details : undefined
        }
      }
    } finally {
      if (context.isCurrent()) modelPending[id] = false
    }
  }

  async function changeConnection(
    connection: ModelConnection,
    action: 'enable' | 'disable' | 'delete'
  ): Promise<void> {
    if (
      busy() ||
      confirmationOpen.value ||
      !(action === 'delete' ? canDeleteCredentials.value : canToggleCredentials.value)
    )
      return
    const context = connectionControlScope.replace()
    if (action !== 'enable') {
      const accepted = await confirmOperation(
        action === 'delete'
          ? '删除该凭据？当前模型或在途请求仍在使用时需要先处理，历史调用记录仍会保留。'
          : '停用后，新请求将不能使用此连接。是否继续？'
      )
      if (!accepted) return
    }
    if (
      !context.isCurrent() ||
      !(action === 'delete' ? canDeleteCredentials.value : canToggleCredentials.value)
    )
      return
    controllingConnection.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      await controlConnection(connection.id, action, context.signal)
      if (context.isCurrent()) await load()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        errorDetails.value = reason instanceof ApiError ? reason.details : undefined
      }
    } finally {
      if (context.isCurrent()) controllingConnection.value = false
    }
  }

  function modelSaved(model: ChatModel, editing: boolean): void {
    const interrupted = loading.value
    scope.replace()
    loading.value = false
    if (editing) models.value = models.value.map((item) => (item.id === model.id ? model : item))
    else models.value.push(model)
    if (interrupted) void load()
  }
  function connectionSaved(connection: ModelConnection, editing: boolean): void {
    const interrupted = loading.value
    scope.replace()
    loading.value = false
    if (editing)
      connections.value = connections.value.map((item) =>
        item.id === connection.id ? connection : item
      )
    else connections.value.push(connection)
    if (interrupted) void load()
  }
  const connectionLabels = computed<Record<string, string>>(() =>
    canReadModels.value
      ? Object.fromEntries(
          models.value.map((model) => [model.id, connectionName(model.connection_id)])
        )
      : {}
  )
  const connectionIssues = computed<Record<string, string>>(() =>
    canReadModels.value
      ? Object.fromEntries(
          models.value.map((model) => [
            model.id,
            model.availability
              ? modelConnectionIssue(model)
              : connectionDisabled(model.connection_id)
                ? '连接已停用'
                : !connectionEnabled(model.connection_id)
                  ? '连接不可用'
                  : ''
          ])
        )
      : {}
  )
  const modelErrorMessages = computed<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(modelFailures).map(([id, failure]) => [id, failure?.message ?? ''])
    )
  )
  const detailPending = computed(() => Boolean(modelPending[detailModelId.value]))
  const detailError = computed(() => modelFailures[detailModelId.value]?.message ?? '')
  const detailErrorDetails = computed(() => modelFailures[detailModelId.value]?.details)
  const detailConnectionLabel = computed(() => connectionLabels.value[detailModelId.value] ?? '')
  const detailConnectionIssue = computed(() => connectionIssues.value[detailModelId.value] ?? '')
  function openDetails(model: ChatModel): void {
    detailModelId.value = model.id
  }
  function closeDetails(): void {
    detailModelId.value = ''
  }
  function setDetailOpen(open: boolean): void {
    if (!open) closeDetails()
  }
  watch(
    () => ({
      authority: access.authority.value,
      createModels: canCreateModels.value,
      updateModels: canUpdateModels.value,
      deleteModels: canDeleteModels.value,
      toggleModels: canToggleModels.value,
      createCredentials: canCreateCredentials.value,
      updateCredentials: canUpdateCredentials.value,
      deleteCredentials: canDeleteCredentials.value,
      toggleCredentials: canToggleCredentials.value,
      readModels: canReadModels.value,
      testModels: canTestModels.value,
      readRates: canReadRates.value,
      readCredentials: canReadCredentials.value
    }),
    (current, previous) => {
      closeConfirmation?.()
      connectionControlScope.replace()
      controllingConnection.value = false
      error.value = ''
      errorDetails.value = undefined
      for (const item of modelScopes.values()) item.replace()
      for (const id of Object.keys(modelPending)) modelPending[id] = false
      for (const id of Object.keys(modelFailures)) delete modelFailures[id]
      if (
        current.authority !== previous.authority ||
        current.readModels !== previous.readModels ||
        current.testModels !== previous.testModels ||
        current.readCredentials !== previous.readCredentials
      ) {
        scope.replace()
        connections.value = []
        connectionChoices.value = []
        models.value = []
        closeDetails()
        lastSuccessAt.value = ''
        void load()
      }
    }
  )

  return {
    modelSearch,
    modelType,
    modelFilters,
    isMobile,
    lastSuccessAt,
    visibleModelColumns,
    connections,
    connectionData,
    models,
    filteredModels,
    connectionEnabled,
    connectionDisabled,
    loading,
    controllingConnection,
    error,
    errorDetails,
    detailModel,
    connectionOptions,
    load,
    copyModelCode,
    changeModel,
    changeConnection,
    modelSaved,
    connectionSaved,
    modelPending,
    modelFailures,
    modelErrorMessages,
    connectionLabels,
    connectionIssues,
    detailPending,
    detailError,
    detailErrorDetails,
    detailConnectionLabel,
    detailConnectionIssue,
    openDetails,
    closeDetails,
    setDetailOpen
  }
}
