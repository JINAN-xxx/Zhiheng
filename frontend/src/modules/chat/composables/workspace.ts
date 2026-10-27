import {
  computed,
  inject,
  nextTick,
  onBeforeUnmount,
  provide,
  reactive,
  readonly,
  ref,
  watch
} from 'vue'
import type { InjectionKey } from 'vue'
import { useRoute } from 'vue-router'

import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { identity } from '../../identity/public.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import type { Skill } from '../../skills/public.ts'
import {
  cancelPreparation,
  getPreparation,
  isPreparation,
  observePreparation,
  projectPreparation,
  preparationFinished
} from '../../knowledge/public.ts'
import type { KnowledgeBase, KnowledgePreparation } from '../../knowledge/public.ts'
import {
  cancelRun,
  createConversation,
  getConversations,
  getConversation,
  updateConversation,
  deleteConversation,
  getMessages,
  getRun,
  observeRun,
  sendMessage
} from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { nextSendIntent } from '../api/sendIntent.ts'
import type { SendIntent } from '../api/sendIntent.ts'
import { uploadFile } from '../../files/public.ts'
import { createChatFileQueue } from '../attachments.ts'
import type { QueuedFile } from '../attachments.ts'

function createChatWorkspace() {
  const conversations = ref<Conversation[]>([])
  const models = ref<ChatModel[]>([])
  const selected = ref<Conversation | null>(null)
  const modelId = ref<string>()
  const messages = ref<ChatMessage[]>([])
  const draft = ref('')
  const queuedFiles = ref<QueuedFile[]>([])
  const queue = createChatFileQueue({
    upload: uploadFile,
    onChange: (files) => (queuedFiles.value = files)
  })
  const archivedFilter = ref(false),
    metadataBusy = ref<string[]>([]),
    metadataUnknown = ref<string[]>([])
  const metadataScope = new RequestScope()
  const supportsVision = computed(
    () => models.value.find((model) => model.id === modelId.value)?.vision_enabled === true
  )
  const fileQueueReady = computed(() =>
    queuedFiles.value.every((file) => file.status === 'ready' && file.fileId)
  )
  const readOnly = computed(
    () =>
      selected.value?.archived === true ||
      (selected.value !== null &&
        [...metadataBusy.value, ...metadataUnknown.value].includes(selected.value.id))
  )
  function addFiles(files: Iterable<File>): void {
    if (sending.value || running.value || readOnly.value) return
    const result = queue.add(files, supportsVision.value)
    if (result.rejected.length)
      error.value = result.rejected.map((item) => `${item.fileName}：${item.error}`).join('；')
  }
  function removeFile(id: string): void {
    if (!sending.value && !running.value) queue.remove(id)
  }
  function retryFile(id: string): void {
    if (!sending.value && !running.value && !readOnly.value) void queue.retry(id)
  }
  const selectedSkills = ref<Skill[]>([])
  const selectedKnowledgeBases = ref<KnowledgeBase[]>([])
  const activePreparation = ref<KnowledgePreparation | null>(null)
  const pendingStops = reactive(new Set<string>())
  const stopOperations = new Map<string, Promise<void>>()
  const preparing = computed(() => {
    const value = activePreparation.value
    return (
      Boolean(value && !preparationFinished(value)) ||
      Boolean(
        selected.value?.preparation_id && (!value || value.id !== selected.value.preparation_id)
      )
    )
  })
  const skillPickerOpen = ref(false)
  const error = ref('')
  const listError = ref('')
  const loading = ref(false)
  const listLoading = ref(false)
  const historyReady = ref(true)
  const sending = ref(false)
  const stopping = ref(false)
  const disconnected = ref(false)
  const activeRun = ref<RunSnapshot | null>(null)
  const stopAcknowledged = computed(() => {
    const preparation = activePreparation.value
    return preparation && !preparationFinished(preparation)
      ? preparation.status === 'cancel_requested'
      : activeRun.value?.status === 'cancel_requested'
  })
  const messagesElement = ref<HTMLElement | null>(null)
  const pageScope = new RequestScope()
  const conversationScope = new RequestScope()
  const running = computed(
    () =>
      preparing.value ||
      Boolean(selected.value && pendingStops.has(selected.value.id)) ||
      (activeRun.value !== null && !isTerminal(activeRun.value))
  )
  const canRecover = computed(() => disconnected.value && running.value)
  const activeRunSources = computed(
    () =>
      messages.value.find(
        (message) => message.role === 'assistant' && message.run_id === activeRun.value?.id
      )?.knowledge_sources ??
      (activePreparation.value?.run?.id === activeRun.value?.id
        ? (activePreparation.value?.hits ?? [])
        : [])
  )
  const modelOptions = computed(() =>
    models.value
      .filter((model) => model.enabled && model.published && model.capabilities.includes('chat'))
      .map((model) => ({ value: model.id, label: model.name }))
  )
  const visibleMessages = computed(() =>
    messages.value.filter(
      (message) => !(message.role === 'assistant' && message.run_id === activeRun.value?.id)
    )
  )
  let pendingSend: SendIntent | null = null
  const canRetry = ref(false)
  onBeforeUnmount(() => {
    metadataScope.dispose()
    queue.clear()
    pageScope.dispose()
    conversationScope.dispose()
    draft.value = ''
    pendingSend = null
    selectedSkills.value = []
    selectedKnowledgeBases.value = []
    activePreparation.value = null
    pendingStops.clear()
    stopOperations.clear()
    skillPickerOpen.value = false
  })
  async function scroll(): Promise<void> {
    await nextTick()
    messagesElement.value?.scrollTo({ top: messagesElement.value.scrollHeight, behavior: 'smooth' })
  }
  function updateRun(run: RunSnapshot): void {
    if (acceptSnapshot(activeRun.value, run)) {
      activeRun.value = run
      if (isTerminal(run) || run.status === 'cancel_requested') pendingStops.delete(run.resource_id)
      void scroll()
    }
  }
  async function follow(
    run: RunSnapshot,
    context: ReturnType<RequestScope['current']>
  ): Promise<void> {
    disconnected.value = false
    if (pendingStops.has(run.resource_id) && !isTerminal(run)) {
      await stop()
      if (!context.isCurrent()) return
      if (activeRun.value?.id === run.id) run = activeRun.value
      if (pendingStops.has(run.resource_id)) throw new Error('停止结果待确认，请恢复查看')
    }
    await observeRun(
      run,
      (snapshot) => {
        if (context.isCurrent()) updateRun(snapshot)
      },
      context.signal
    )
    if (context.isCurrent() && selected.value) {
      messages.value = await getMessages(selected.value.id, context.signal)
      if (context.isCurrent()) await scroll()
    }
  }
  async function acceptMainRun(
    run: RunSnapshot,
    context: ReturnType<RequestScope['current']>
  ): Promise<void> {
    const conversation = selected.value
    if (!conversation || run.resource_type !== 'chat' || run.resource_id !== conversation.id)
      throw new Error('对话运行与当前会话不匹配')
    if (activeRun.value?.id === run.id) updateRun(run)
    else {
      activeRun.value = null
      updateRun(run)
    }
    pendingSend = null
    queue.clear()
    draft.value = ''
    selectedSkills.value = []
    selectedKnowledgeBases.value = []
    messages.value = await getMessages(conversation.id, context.signal)
    if (!context.isCurrent()) return
    sending.value = false
    await scroll()
    if (activeRun.value?.id === run.id) run = activeRun.value
    await follow(run, context)
  }
  function updatePreparation(value: KnowledgePreparation): KnowledgePreparation {
    if (value.resource_id !== selected.value?.id || value.resource_type !== 'chat')
      throw new Error('知识检索准备与当前会话不匹配')
    value = projectPreparation(activePreparation.value, value)
    activePreparation.value = value
    const preparationId = preparationFinished(value) ? null : value.id
    selected.value.preparation_id = preparationId
    const listed = conversations.value.find((item) => item.id === value.resource_id)
    if (listed) listed.preparation_id = preparationId
    if (
      value.status === 'cancel_requested' ||
      (preparationFinished(value) && value.status !== 'admitted')
    )
      pendingStops.delete(value.resource_id)
    return value
  }
  async function followKnowledge(
    initial: KnowledgePreparation,
    context: ReturnType<RequestScope['current']>
  ): Promise<void> {
    if (initial.resource_type !== 'chat' || initial.resource_id !== selected.value?.id)
      throw new Error('知识检索准备与当前会话不匹配')
    initial = updatePreparation(initial)
    if (pendingStops.has(initial.resource_id) && !preparationFinished(initial)) {
      await stop()
      if (!context.isCurrent()) return
      if (pendingStops.has(initial.resource_id))
        throw new Error(error.value || '停止结果待确认，请恢复查看')
      initial = activePreparation.value ?? initial
    }
    disconnected.value = false
    sending.value = false
    const value = await observePreparation(
      initial,
      (snapshot) => {
        if (context.isCurrent()) return updatePreparation(snapshot)
      },
      context.signal
    )
    if (!context.isCurrent()) return
    if (value.run) await acceptMainRun(value.run, context)
    else if (value.status === 'cancelled') error.value = '知识检索已取消'
    else error.value = value.error?.message ?? '知识检索未完成，请检查知识库后重试'
  }
  async function selectConversation(conversation: Conversation | null): Promise<void> {
    const context = conversationScope.replace()
    selected.value = conversation
    queue.clear()
    messages.value = []
    activeRun.value = null
    activePreparation.value = null
    error.value = ''
    draft.value = ''
    selectedSkills.value = []
    selectedKnowledgeBases.value = []
    skillPickerOpen.value = false
    canRetry.value = false
    pendingSend = null
    disconnected.value = false
    sending.value = false
    stopping.value = false
    loading.value = false
    historyReady.value = conversation === null
    if (!conversation) return
    loading.value = true
    try {
      const history = await getMessages(conversation.id, context.signal)
      if (!context.isCurrent()) return
      messages.value = history
      if (conversation.preparation_id) {
        historyReady.value = true
        const preparation = await getPreparation(conversation.preparation_id, context.signal)
        if (!context.isCurrent()) return
        historyReady.value = true
        loading.value = false
        await followKnowledge(preparation, context)
        return
      }
      const last = history.at(-1)
      if (last?.role === 'user' && last.run_id) {
        const run = await getRun(last.run_id, context.signal)
        if (!context.isCurrent()) return
        activeRun.value = run
        historyReady.value = true
        loading.value = false
        await follow(run, context)
      } else {
        historyReady.value = true
      }
      if (context.isCurrent()) await scroll()
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        disconnected.value = running.value
      }
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
  async function load(): Promise<void> {
    const context = pageScope.replace()
    listLoading.value = true
    listError.value = ''
    try {
      const [availableModels, items] = await Promise.all([
        getModels(context.signal),
        getConversations(context.signal, archivedFilter.value)
      ])
      if (!context.isCurrent()) return
      models.value = availableModels
      conversations.value = items
      const current = selected.value
      const listed = current ? items.find((item) => item.id === current.id) : undefined
      if (listed && current && listed.version >= current.version) selected.value = listed
      const detailIds = new Set(metadataUnknown.value)
      if (current && !listed) detailIds.add(current.id)
      for (const id of detailIds) {
        try {
          const value = await getConversation(id, context.signal)
          if (!context.isCurrent()) return
          if (selected.value?.id === id && value.version >= selected.value.version)
            selected.value = value
          metadataUnknown.value = metadataUnknown.value.filter((item) => item !== id)
        } catch (reason) {
          if (!context.isCurrent()) return
          if (reason instanceof ApiError && reason.status === 404) {
            if (selected.value?.id === id) await selectConversation(null)
            metadataUnknown.value = metadataUnknown.value.filter((item) => item !== id)
          } else throw reason
        }
      }
      const preparationId = selected.value?.preparation_id
      if (
        selected.value &&
        preparationId &&
        (activePreparation.value?.id !== preparationId || disconnected.value)
      ) {
        void restorePreparation(selected.value.id, preparationId)
      }
      if (!modelOptions.value.some((model) => model.value === modelId.value)) {
        modelId.value = modelOptions.value[0]?.value
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) listError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) listLoading.value = false
    }
  }
  async function restorePreparation(conversationId: string, preparationId: string): Promise<void> {
    const previous = conversationScope.current()
    const operation = stopOperations.get(conversationId)
    if (operation) await operation
    if (!previous.isCurrent() || selected.value?.id !== conversationId) return
    const context = conversationScope.replace()
    loading.value = true
    sending.value = false
    canRetry.value = false
    pendingSend = null
    error.value = ''
    try {
      if (!historyReady.value) {
        const history = await getMessages(conversationId, context.signal)
        if (!context.isCurrent()) return
        messages.value = history
        historyReady.value = true
      }
      const value = await getPreparation(preparationId, context.signal)
      if (!context.isCurrent() || selected.value?.id !== conversationId) return
      loading.value = false
      await followKnowledge(value, context)
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        disconnected.value = true
      }
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
  const route = useRoute()
  watch(
    () => route.name,
    (name, previous) => {
      if (name && (previous === undefined || name === 'chat')) void load()
    },
    { immediate: true }
  )
  async function send(retry = false): Promise<void> {
    const model = modelId.value
    const message = draft.value.trim() || (queuedFiles.value.length ? '请分析上传的文件。' : '')
    if (
      sending.value ||
      readOnly.value ||
      running.value ||
      loading.value ||
      listLoading.value ||
      !historyReady.value ||
      (!retry && (!message || !model || !fileQueueReady.value))
    )
      return
    if (
      !retry &&
      queuedFiles.value.some((file) => file.kind === 'image') &&
      !supportsVision.value
    ) {
      error.value = '请选择已启用视觉能力的模型后发送图片'
      return
    }
    const ownerId = identity.value?.id
    if (!ownerId) return
    const context = conversationScope.replace()
    const isCurrent = () => context.isCurrent() && identity.value?.id === ownerId
    const request = retry
      ? pendingSend
      : model
        ? nextSendIntent(
            pendingSend,
            {
              conversationId: selected.value?.id ?? null,
              model_id: model,
              message,
              skill_slugs: selectedSkills.value.map((skill) => skill.skillSlug),
              file_ids: queuedFiles.value.map((file) => file.fileId!),
              knowledge_base_ids: selectedKnowledgeBases.value.map((base) => base.id)
            },
            () => crypto.randomUUID()
          )
        : null
    if (!request) return
    pendingSend = request
    sending.value = true
    skillPickerOpen.value = false
    error.value = ''
    canRetry.value = false
    try {
      let conversationId = request.conversationId
      if (conversationId === null) {
        const conversation = await createConversation(context.signal)
        if (!isCurrent()) return
        pageScope.replace()
        listLoading.value = false
        selected.value = conversation
        conversations.value.unshift(conversation)
        conversationId = conversation.id
        pendingSend = { ...request, conversationId }
      }
      const admission = await sendMessage(
        conversationId,
        {
          model_id: request.model_id,
          message: request.message,
          client_request_id: request.client_request_id,
          skill_slugs: request.skill_slugs ?? [],
          file_ids: request.file_ids ?? [],
          knowledge_base_ids: request.knowledge_base_ids ?? []
        },
        context.signal
      )
      if (!isCurrent()) return
      pendingSend = null
      if (isPreparation(admission)) {
        if (activeRun.value?.id !== admission.run?.id) activeRun.value = null
        await followKnowledge(admission, context)
      } else await acceptMainRun(admission, context)
    } catch (reason) {
      if (isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        canRetry.value = pendingSend !== null
        disconnected.value = running.value
      }
    } finally {
      if (isCurrent()) sending.value = false
    }
  }
  function stop(): Promise<void> {
    const conversationId = selected.value?.id
    if (!conversationId) return Promise.resolve()
    const existing = stopOperations.get(conversationId)
    if (existing) return existing
    const run = activeRun.value
    const preparation = activePreparation.value
    if (stopAcknowledged.value) return Promise.resolve()
    const preparationId =
      selected.value?.preparation_id ??
      (!run && preparation && !preparationFinished(preparation) ? preparation.id : null)
    if (!preparationId && (!run || isTerminal(run))) {
      pendingStops.delete(conversationId)
      return Promise.resolve()
    }
    pendingStops.add(conversationId)
    const context = conversationScope.current()
    stopping.value = true
    const operation = (async () => {
      try {
        if (preparationId) {
          const result = await cancelPreparation(preparationId, context.signal)
          if (!context.isCurrent()) return
          updatePreparation(result)
          if (result.run) {
            if (activeRun.value?.id !== result.run.id) activeRun.value = null
            updateRun(result.run)
            if (!isTerminal(result.run) && result.run.status !== 'cancel_requested') {
              const stopped = await cancelRun(result.run.id, context.signal)
              if (context.isCurrent()) updateRun(stopped)
            }
          }
        } else if (run) {
          const result = await cancelRun(run.id, context.signal)
          if (context.isCurrent()) updateRun(result)
        }
      } catch (reason) {
        if (context.isCurrent() && !isAborted(reason)) {
          error.value = errorMessage(reason)
          disconnected.value = true
        }
      } finally {
        if (context.isCurrent()) stopping.value = false
      }
    })()
    stopOperations.set(conversationId, operation)
    void operation.then(() => {
      if (stopOperations.get(conversationId) === operation) stopOperations.delete(conversationId)
    })
    return operation
  }
  async function reconnect(): Promise<void> {
    const owner = conversationScope.current(),
      conversationId = selected.value?.id
    const operation = conversationId ? stopOperations.get(conversationId) : null
    if (operation) await operation
    if (!owner.isCurrent()) return
    const run = activeRun.value
    const preparation = activePreparation.value
    const preparationId =
      selected.value?.preparation_id ??
      (preparation && !preparationFinished(preparation) ? preparation.id : null)
    if (!run && !preparationId) return
    const context = conversationScope.replace()
    error.value = ''
    try {
      if (preparationId) {
        const latest = await getPreparation(preparationId, context.signal)
        if (context.isCurrent()) await followKnowledge(latest, context)
      } else if (run) {
        const latest = await getRun(run.id, context.signal)
        if (!context.isCurrent()) return
        updateRun(latest)
        await follow(latest, context)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        disconnected.value = true
      }
    }
  }
  function setArchivedFilter(value: boolean): void {
    archivedFilter.value = value
    void load()
  }
  function conversationLocked(id: string): boolean {
    return (
      metadataBusy.value.length > 0 ||
      metadataUnknown.value.includes(id) ||
      pendingStops.has(id) ||
      Boolean(conversations.value.find((item) => item.id === id)?.preparation_id) ||
      (selected.value?.id === id && (sending.value || running.value || canRetry.value))
    )
  }
  async function changeConversation(
    conversation: Conversation,
    change: { title?: string; archived?: boolean } | 'delete'
  ): Promise<boolean> {
    if (conversationLocked(conversation.id)) return false
    const context = metadataScope.replace(),
      target = { ...conversation },
      owner = identity.value?.id
    metadataBusy.value = [target.id]
    listError.value = ''
    try {
      const result =
        change === 'delete'
          ? await deleteConversation(target, context.signal)
          : await updateConversation(target, change, context.signal)
      if (!context.isCurrent() || identity.value?.id !== owner) return false
      pageScope.replace()
      listLoading.value = false
      if (result) {
        conversations.value = conversations.value.filter((item) => item.id !== target.id)
        if (result.archived === archivedFilter.value) conversations.value.unshift(result)
        if (selected.value?.id === target.id) selected.value = result
      } else {
        conversations.value = conversations.value.filter((item) => item.id !== target.id)
        if (selected.value?.id === target.id) await selectConversation(null)
      }
      return true
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        if (!(reason instanceof ApiError) || reason.status >= 500)
          metadataUnknown.value = [...new Set([...metadataUnknown.value, target.id])]
        listError.value = `${errorMessage(reason)}；请刷新会话核对状态。`
      }
      return false
    } finally {
      if (context.isCurrent()) metadataBusy.value = []
    }
  }
  function setModelId(value: unknown): void {
    modelId.value = typeof value === 'string' ? value : undefined
  }
  function keydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      void send()
    }
  }
  function removeSkill(slug: string): void {
    if (!sending.value && !running.value)
      selectedSkills.value = selectedSkills.value.filter((skill) => skill.skillSlug !== slug)
  }
  function handleSkillSelected(): void {
    draft.value = draft.value.replace(/(^|\s)@$/, '$1').trimEnd()
  }
  watch(draft, (value) => {
    if (!sending.value && !running.value && !loading.value && /(^|\s)@$/.test(value))
      skillPickerOpen.value = true
  })

  return {
    selectedKnowledgeBases,
    activePreparation,
    activeRunSources,
    preparing,
    conversations,
    queuedFiles,
    addFiles,
    removeFile,
    retryFile,
    supportsVision,
    fileQueueReady,
    readOnly,
    archivedFilter,
    setArchivedFilter,
    conversationLocked,
    changeConversation,
    selected,
    modelId,
    messages,
    draft,
    selectedSkills,
    skillPickerOpen,
    removeSkill,
    handleSkillSelected,
    error,
    listError,
    loading,
    listLoading,
    historyReady,
    sending,
    stopping,
    stopAcknowledged,
    activeRun,
    messagesElement,
    running,
    canRecover,
    modelOptions,
    visibleMessages,
    canRetry,
    load,
    selectConversation,
    send,
    stop,
    reconnect,
    setModelId,
    keydown
  }
}
type ChatWorkspace = ReturnType<typeof createChatWorkspace>
const workspaceKey: InjectionKey<ChatWorkspace> = Symbol('chat-workspace')
export function provideChatWorkspace(): void {
  provide(workspaceKey, createChatWorkspace())
}
export function useChatWorkspace(): ChatWorkspace {
  const state = inject(workspaceKey)
  if (!state) throw new Error('Chat workspace provider is required')
  return state
}
export function useChatNavigation() {
  const state = useChatWorkspace()
  return {
    conversations: readonly(state.conversations),
    selected: readonly(state.selected),
    loading: readonly(state.listLoading),
    error: readonly(state.listError),
    archivedFilter: readonly(state.archivedFilter),
    setArchivedFilter: state.setArchivedFilter,
    conversationLocked: state.conversationLocked,
    changeConversation: state.changeConversation,
    refresh: state.load,
    select: state.selectConversation
  }
}
