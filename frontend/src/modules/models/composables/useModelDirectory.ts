import { copyText } from '../../../shared/clipboard.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Modal, message as notice } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { useWindowSize } from '@vueuse/core'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import {
  createConnection,
  createModel,
  getConnections,
  getModels,
  getTrialModels,
  observeModelTest,
  startModelTest,
  updateConnection,
  updateModel,
  controlConnection,
  controlModel
} from '../api/index.ts'
import type { ChatModel, ModelConnection } from '../api/index.ts'
import { modelTypeLabel } from '../presentation.ts'
import type { ModelControlAction } from '../presentation.ts'

export function useModelDirectory() {
  const canReadModels = computed(() =>
    hasAnyPermission(identity.value, 'ai:models:read', 'ai:models:write')
  )
  const canWriteModels = computed(() => hasAnyPermission(identity.value, 'ai:models:write'))
  const trialDenied = ref(false)
  const canTestModels = computed(
    () => !trialDenied.value && hasAnyPermission(identity.value, 'ai:models:test')
  )
  function denyTrialAccess() {
    trialDenied.value = true
    void notice.error('模型试用权限已变化，请刷新页面后查看')
  }
  const canReadRates = computed(() =>
    hasAnyPermission(identity.value, 'ai:rates:read', 'ai:rates:write')
  )
  const canWriteRates = computed(() => hasAnyPermission(identity.value, 'ai:rates:write'))
  const hasModelActions = computed(
    () => canWriteModels.value || canTestModels.value || canReadRates.value
  )
  function setActiveTab(value: unknown): void {
    activeTab.value = value === 'credentials' && canReadModels.value ? 'credentials' : 'platform'
  }
  const activeTab = ref('platform')
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
    return connections.value.find((connection) => connection.id === id)?.name ?? '连接不可用'
  }
  function connectionEnabled(id: string): boolean {
    return connections.value.some((connection) => connection.id === id && connection.enabled)
  }
  function connectionDisabled(id: string): boolean {
    return connections.value.find((connection) => connection.id === id)?.enabled === false
  }
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const errorDetails = ref<unknown>()
  const modalError = ref('')
  const connectionOpen = ref(false)
  const modelOpen = ref(false)
  const editingConnection = ref<ModelConnection | null>(null)
  const editingModel = ref<ChatModel | null>(null)
  const detailModelId = ref('')
  const detailModel = computed(
    () => models.value.find((model) => model.id === detailModelId.value) ?? null
  )
  const rateModel = ref<ChatModel | null>(null)
  const connectionForm = reactive({ name: '', base_url: '', api_key: '' })
  const modelForm = reactive<{
    name: string
    connection_id: string
    upstream_model: string
    max_output_tokens: number | null
    context_window_tokens: number | null
    model_type: ChatModel['model_type']
    vision_enabled: boolean
  }>({
    name: '',
    connection_id: '',
    upstream_model: '',
    max_output_tokens: 512,
    context_window_tokens: null,
    model_type: 'chat',
    vision_enabled: false
  })
  const testRun = ref<RunSnapshot | null>(null)
  const testingModel = ref('')
  const testDisconnected = ref(false)
  const scope = new RequestScope()
  const mutationScope = new RequestScope()
  const testScope = new RequestScope()
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
  let testIntent: { modelId: string; requestId: string } | null = null
  const testBusy = computed(
    () =>
      testingModel.value !== '' &&
      !testDisconnected.value &&
      (testRun.value === null || !isTerminal(testRun.value))
  )
  const connectionOptions = computed(() =>
    connections.value
      .filter((item) => item.enabled)
      .map((item) => ({ value: item.id, label: item.name }))
  )
  onBeforeUnmount(() => {
    scope.dispose()
    mutationScope.dispose()
    testScope.dispose()
    for (const item of modelScopes.values()) item.dispose()
    modelScopes.clear()
    closeConfirmation?.()
    connectionForm.api_key = ''
  })
  async function load(): Promise<void> {
    const context = scope.replace()
    loading.value = true
    error.value = ''
    errorDetails.value = undefined
    try {
      const [items, availableModels] = await Promise.all([
        canReadModels.value ? getConnections(context.signal) : Promise.resolve([]),
        canReadModels.value
          ? getModels(context.signal, true)
          : canTestModels.value
            ? getTrialModels(context.signal)
            : getModels(context.signal)
      ])
      if (context.isCurrent()) {
        connections.value = items
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
  function openConnection(connection: ModelConnection | null = null): void {
    if (!canWriteModels.value) return
    mutationScope.replace()
    saving.value = false
    editingConnection.value = connection
    Object.assign(connectionForm, {
      name: connection?.name ?? '',
      base_url: connection?.base_url ?? '',
      api_key: ''
    })
    connectionOpen.value = true
    modalError.value = ''
  }
  function openModel(model: ChatModel | null = null): void {
    if (!canWriteModels.value || (model && modelPending[model.id])) return
    mutationScope.replace()
    saving.value = false
    editingModel.value = model
    Object.assign(modelForm, {
      name: model?.name ?? '',
      connection_id:
        model?.connection_id ??
        (connections.value.filter((item) => item.enabled).length === 1
          ? (connections.value.find((item) => item.enabled)?.id ?? '')
          : ''),
      upstream_model: model?.upstream_model ?? '',
      max_output_tokens: model?.max_output_tokens ?? 512,
      context_window_tokens: model?.context_window_tokens ?? null,
      model_type: model?.model_type ?? 'chat',
      vision_enabled: model?.vision_enabled ?? false
    })
    modelOpen.value = true
    modalError.value = ''
  }
  function closeConnection(): void {
    connectionOpen.value = false
    connectionForm.api_key = ''
    mutationScope.replace()
    saving.value = false
    modalError.value = ''
  }
  async function saveConnection(): Promise<void> {
    if (saving.value || !canWriteModels.value) return
    const editing = editingConnection.value
    if (
      !connectionForm.name.trim() ||
      !connectionForm.base_url.trim() ||
      (!editing && !connectionForm.api_key)
    ) {
      modalError.value = '请填写连接名称、API 地址和密钥'
      return
    }
    const context = mutationScope.replace()
    saving.value = true
    modalError.value = ''
    try {
      const body = { name: connectionForm.name.trim(), base_url: connectionForm.base_url.trim() }
      const connection = editing
        ? await updateConnection(
            editing.id,
            { ...body, ...(connectionForm.api_key ? { api_key: connectionForm.api_key } : {}) },
            context.signal
          )
        : await createConnection({ ...body, api_key: connectionForm.api_key }, context.signal)
      if (context.isCurrent()) {
        scope.replace()
        loading.value = false
        if (editing)
          connections.value = connections.value.map((item) =>
            item.id === connection.id ? connection : item
          )
        else connections.value.push(connection)
        connectionOpen.value = false
        if (!editing) activeTab.value = 'platform'
        connectionForm.name = ''
        connectionForm.base_url = ''
        modelForm.connection_id = connection.id
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) modalError.value = errorMessage(reason)
    } finally {
      connectionForm.api_key = ''
      if (context.isCurrent()) saving.value = false
    }
  }
  function closeModel(): void {
    modelOpen.value = false
    mutationScope.replace()
    saving.value = false
    modalError.value = ''
  }
  function setConnectionId(value: unknown): void {
    modelForm.connection_id = typeof value === 'string' ? value : ''
  }
  function setMaxOutputTokens(value: unknown): void {
    modelForm.max_output_tokens = typeof value === 'number' ? value : null
  }
  function setModelType(value: unknown): void {
    if (value === 'chat' || value === 'embedding' || value === 'rerank')
      modelForm.model_type = value
  }
  function confirmOperation(title: string): Promise<boolean> {
    confirmationOpen.value = true
    return new Promise((resolve) => {
      const finish = (accepted: boolean) => {
        confirmationOpen.value = false
        closeConfirmation = undefined
        resolve(accepted)
      }
      const dialog = Modal.confirm({
        title,
        okText: '确定',
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
    if (saving.value || confirmationOpen.value || modelPending[id] || !canWriteModels.value) return
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
      if (!context.isCurrent() || !canWriteModels.value) return
      error.value = ''
      errorDetails.value = undefined
      const updated = await controlModel(id, action, context.signal)
      if (!context.isCurrent()) return
      const interrupted = loading.value
      scope.replace()
      loading.value = false
      if (action === 'delete') {
        models.value = models.value.filter((item) => item.id !== id)
        if (detailModelId.value === id) closeDetails()
        if (rateModel.value?.id === id) rateModel.value = null
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
    if (saving.value || confirmationOpen.value || !canWriteModels.value) return
    const context = mutationScope.replace()
    if (action !== 'enable') {
      const accepted = await confirmOperation(
        action === 'delete'
          ? '删除该凭据？当前模型或在途请求仍在使用时需要先处理，历史调用记录仍会保留。'
          : '停用后，新请求将不能使用此连接。是否继续？'
      )
      if (!accepted) return
    }
    if (!context.isCurrent() || !canWriteModels.value) return
    saving.value = true
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
      if (context.isCurrent()) saving.value = false
    }
  }

  async function saveModel(): Promise<void> {
    if (saving.value || !canWriteModels.value) return
    if (!modelForm.upstream_model.trim()) {
      modalError.value = '请填写上游模型标识'
      return
    }
    if (!modelForm.name.trim()) {
      modalError.value = '请填写模型显示名称'
      return
    }
    if (!modelForm.connection_id) {
      modalError.value = '请选择模型连接'
      return
    }
    const maxOutputTokens = modelForm.max_output_tokens
    if (maxOutputTokens === null || !Number.isSafeInteger(maxOutputTokens) || maxOutputTokens < 1) {
      modalError.value = '输出上限须为正整数'
      return
    }
    const windowTokens = modelForm.context_window_tokens
    if (
      windowTokens !== null &&
      (!Number.isInteger(windowTokens) || windowTokens < 256 || windowTokens > 2097152)
    ) {
      modalError.value = '上下文窗口须为 256–2097152 的整数，或留空使用平台输入预算'
      return
    }
    const context = mutationScope.replace()
    saving.value = true
    modalError.value = ''
    try {
      const body = {
        ...modelForm,
        vision_enabled: modelForm.model_type === 'chat' && modelForm.vision_enabled,
        max_output_tokens: maxOutputTokens,
        name: modelForm.name.trim(),
        upstream_model: modelForm.upstream_model.trim()
      }
      const model = editingModel.value
        ? await updateModel(
            editingModel.value.id,
            {
              name: body.name,
              connection_id: body.connection_id,
              upstream_model: body.upstream_model,
              max_output_tokens: body.max_output_tokens,
              context_window_tokens: body.context_window_tokens,
              vision_enabled: body.model_type === 'chat' && body.vision_enabled
            },
            context.signal
          )
        : await createModel(body, context.signal)
      if (context.isCurrent()) {
        scope.replace()
        loading.value = false
        if (editingModel.value)
          models.value = models.value.map((item) => (item.id === model.id ? model : item))
        else models.value.push(model)
        modelOpen.value = false
        modelForm.name = ''
        modelForm.upstream_model = ''
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) modalError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) saving.value = false
    }
  }
  async function followTest(
    initial: RunSnapshot,
    context: ReturnType<RequestScope['current']>
  ): Promise<RunSnapshot> {
    return observeModelTest(
      initial,
      (run) => {
        if (context.isCurrent() && acceptSnapshot(testRun.value, run)) testRun.value = run
      },
      context.signal
    )
  }
  async function testModel(model: ChatModel): Promise<void> {
    if (
      testBusy.value ||
      modelPending[model.id] ||
      !canTestModels.value ||
      !model.enabled ||
      connectionDisabled(model.connection_id)
    )
      return
    const context = testScope.replace()
    const previousRun =
      testDisconnected.value && testingModel.value === model.id ? testRun.value : null
    testingModel.value = model.id
    testRun.value = previousRun
    testDisconnected.value = false
    error.value = ''
    errorDetails.value = undefined
    delete modelFailures[model.id]
    try {
      let run = previousRun
      if (!run) {
        const intent =
          testIntent?.modelId === model.id
            ? testIntent
            : { modelId: model.id, requestId: createRequestId() }
        testIntent = intent
        run = await startModelTest(model.id, intent.requestId, context.signal)
      }
      if (!context.isCurrent()) return
      testIntent = null
      testRun.value = run
      const result = await followTest(run, context)
      if (!context.isCurrent()) return
      if (result.status === 'completed') void notice.success('测试成功')
      else {
        const message = result.error?.message || '测试失败'
        modelFailures[model.id] = { message }
        void notice.error(message)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        if (reason instanceof ApiError && reason.status === 403 && reason.code === 'forbidden')
          denyTrialAccess()
        testDisconnected.value = testRun.value !== null && !isTerminal(testRun.value)
        if (!testRun.value) testingModel.value = ''
        const message = testDisconnected.value
          ? '测试结果获取失败，请再次点击测试查看结果'
          : errorMessage(reason)
        modelFailures[model.id] = {
          message,
          details: reason instanceof ApiError ? reason.details : undefined
        }
        void notice.error(message)
      }
    }
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
            connectionDisabled(model.connection_id)
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
  function toggleModel(model: ChatModel): void {
    void changeModel(model, model.enabled ? 'disable' : 'enable')
  }
  function toggleDetails(): void {
    if (detailModel.value) toggleModel(detailModel.value)
  }
  function copyDetails(): void {
    if (detailModel.value) void copyModelCode(detailModel.value.id)
  }
  function editDetails(): void {
    const model = detailModel.value
    if (model) {
      closeDetails()
      openModel(model)
    }
  }
  function ratesDetails(): void {
    const model = detailModel.value
    if (model && canReadRates.value) {
      closeDetails()
      rateModel.value = model
    }
  }
  function testDetails(): void {
    if (detailModel.value) void testModel(detailModel.value)
  }
  function controlDetails(action: 'delete'): void {
    if (detailModel.value) void changeModel(detailModel.value, action)
  }

  watch(
    [canReadModels, canWriteModels, canTestModels, canReadRates],
    ([read, write, test, rates], previous) => {
      closeConfirmation?.()
      error.value = ''
      errorDetails.value = undefined
      modalError.value = ''
      for (const id of Object.keys(modelFailures)) delete modelFailures[id]
      if (!write) {
        mutationScope.replace()
        saving.value = false
        modelOpen.value = false
        connectionOpen.value = false
        connectionForm.api_key = ''
        for (const item of modelScopes.values()) item.replace()
        for (const id of Object.keys(modelPending)) modelPending[id] = false
      }
      if (!test) {
        testScope.replace()
        testRun.value = null
        testingModel.value = ''
        testIntent = null
        testDisconnected.value = false
      }
      if (!rates) rateModel.value = null
      if (read !== previous[0] || test !== previous[2]) {
        scope.replace()
        connections.value = []
        models.value = []
        closeDetails()
        activeTab.value = 'platform'
        rateModel.value = null
        void load()
      }
    }
  )

  return {
    canReadModels,
    canWriteModels,
    canTestModels,
    denyTrialAccess,
    canReadRates,
    canWriteRates,
    setActiveTab,
    activeTab,
    modelSearch,
    modelType,
    modelFilters,
    isMobile,
    lastSuccessAt,
    visibleModelColumns,
    connections,
    models,
    filteredModels,
    connectionEnabled,
    connectionDisabled,
    loading,
    saving,
    error,
    errorDetails,
    modalError,
    connectionOpen,
    modelOpen,
    editingConnection,
    editingModel,
    detailModel,
    rateModel,
    connectionForm,
    modelForm,
    testingModel,
    testBusy,
    connectionOptions,
    load,
    copyModelCode,
    openConnection,
    openModel,
    closeConnection,
    saveConnection,
    closeModel,
    setConnectionId,
    setMaxOutputTokens,
    setModelType,
    modelTypeLabel,
    changeModel,
    changeConnection,
    saveModel,
    testModel,
    modelPending,
    modelErrorMessages,
    connectionLabels,
    connectionIssues,
    detailPending,
    detailError,
    detailErrorDetails,
    detailConnectionLabel,
    detailConnectionIssue,
    openDetails,
    setDetailOpen,
    toggleModel,
    toggleDetails,
    copyDetails,
    editDetails,
    ratesDetails,
    testDetails,
    controlDetails
  }
}
