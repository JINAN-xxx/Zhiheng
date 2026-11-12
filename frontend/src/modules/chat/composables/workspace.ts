import { useChatContextObservation } from './contextObservation.ts'
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
import { message as toast } from 'ant-design-vue'

import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
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
import type { KnowledgePreparation } from '../../knowledge/public.ts'
import {
  cancelRun,
  createConversation,
  getConversationPage,
  getConversation,
  updateConversation,
  deleteConversation,
  getMessagePage,
  getRun,
  observeRun,
  sendMessage
} from '../api/index.ts'
import { uploadChatAttachment } from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { nextSendIntent } from '../api/sendIntent.ts'
import type { SendIntent } from '../api/sendIntent.ts'
import {
  defaultGenerationSettings,
  generationRequest,
  generationSettingsError
} from '../generationSettings.ts'
import type { ChatGenerationSettings } from '../generationSettings.ts'
import { regenerateMessage, setMessageFeedback } from '../api/messageActions.ts'
import type { FeedbackValue } from '../api/messageActions.ts'
import { groupMessageVersions, nextRegenerationIntent } from './messageVersions.ts'
import type { RegenerationIntent } from './messageVersions.ts'
import { createChatFileQueue } from '../attachments.ts'
import type { QueuedFile } from '../attachments.ts'
import { inputIssueFrom } from '../inputIssue.ts'
import type { ChatInputIssue } from '../inputIssue.ts'

function createChatWorkspace() {
  const conversations = ref<Conversation[]>([])
  const historySearch = ref(''),
    conversationCursor = ref<string | null>(null),
    moreConversationsLoading = ref(false)
  const olderCursor = ref<string | null>(null),
    olderLoading = ref(false),
    olderError = ref('')
  const listMoreScope = new RequestScope(),
    olderScope = new RequestScope()
  let searchTimer: ReturnType<typeof setTimeout> | undefined

  const models = ref<ChatModel[]>([])
  const selected = ref<Conversation | null>(null)
  const modelId = ref<string>()
  let modelNeedsSelection = false
  const generationSettings = ref<ChatGenerationSettings>(defaultGenerationSettings())
  const selectedModel = computed(
    () => models.value.find((model) => model.id === modelId.value) ?? null
  )
  const messages = ref<ChatMessage[]>([])
  const draft = ref('')
  const queuedFiles = ref<QueuedFile[]>([])
  const queue = createChatFileQueue({
    upload: uploadChatAttachment,
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
      void toast.warning({
        key: 'chat-file-validation',
        content: [...new Set(result.rejected.map((item) => item.error))].join('；'),
        duration: 3
      })
  }
  function removeFile(id: string): void {
    if (!sending.value && !running.value) queue.remove(id)
  }
  function retryFile(id: string): void {
    if (!sending.value && !running.value && !readOnly.value) void queue.retry(id)
  }
  const selectedSkills = ref<Skill[]>([])
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
  const inputIssue = ref<ChatInputIssue | null>(null)
  const dismissInputIssue = () => {
    inputIssue.value = null
  }
  watch(
    [
      draft,
      modelId,
      generationSettings,
      () => queuedFiles.value.map((file) => file.fileId ?? file.id).join(','),
      () => selectedSkills.value.map((skill) => skill.skillSlug).join(',')
    ],
    dismissInputIssue
  )
  const listError = ref('')
  const loading = ref(false)
  const listLoading = ref(false)
  const historyReady = ref(true)
  const sending = ref(false)
  const stopping = ref(false)
  const disconnected = ref(false)
  const activeRun = ref<RunSnapshot | null>(null)
  const contextObservation = useChatContextObservation(selected, activeRun)
  const stopAcknowledged = computed(() => {
    const preparation = activePreparation.value
    return preparation && !preparationFinished(preparation)
      ? preparation.status === 'cancel_requested'
      : activeRun.value?.status === 'cancel_requested'
  })
  const messagesElement = ref<HTMLElement | null>(null)
  const pageScope = new RequestScope()
  const conversationScope = new RequestScope()
  const messageActionScope = new RequestScope()
  const feedbackBusy = reactive(new Set<string>())
  const confirmedFeedback = new Map<string, { revision: number; value: FeedbackValue }>()
  let feedbackRevision = 0
  const pendingRegenerations = reactive(new Map<string, RegenerationIntent>())
  const preparationComposerPolicies = new Map<string, boolean>()
  const regenerationPending = computed(() =>
    Boolean(selected.value && pendingRegenerations.has(selected.value.id))
  )
  const canRetryRegeneration = computed(
    () => regenerationPending.value && !sending.value && !running.value
  )
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
      .filter((model) => model.enabled && model.capabilities.includes('chat'))
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
    dismissInputIssue()
    clearTimeout(searchTimer)
    listMoreScope.dispose()
    olderScope.dispose()
    metadataScope.dispose()
    queue.clear()
    pageScope.dispose()
    conversationScope.dispose()
    messageActionScope.dispose()
    pendingRegenerations.clear()
    preparationComposerPolicies.clear()
    feedbackBusy.clear()
    confirmedFeedback.clear()
    draft.value = ''
    generationSettings.value = defaultGenerationSettings()
    pendingSend = null
    selectedSkills.value = []
    activePreparation.value = null
    pendingStops.clear()
    stopOperations.clear()
    skillPickerOpen.value = false
  })
  async function scroll(force = false): Promise<void> {
    const element = messagesElement.value
    const follow =
      force || !element || element.scrollHeight - element.scrollTop - element.clientHeight < 180
    await nextTick()
    if (follow)
      messagesElement.value?.scrollTo({
        top: messagesElement.value.scrollHeight,
        behavior: 'smooth'
      })
  }
  function mergeMessages(previous: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
    const byId = new Map(previous.map((item) => [item.id, item]))
    for (const item of incoming) byId.set(item.id, item)
    return [...byId.values()].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  }
  async function readMessages(conversationId: string, signal: AbortSignal): Promise<ChatMessage[]> {
    const requestedRevision = feedbackRevision
    const previous = messages.value
    const known = new Set(previous.map((item) => item.id))
    let page = await getMessagePage(conversationId, signal)
    let history = page.items
    while (previous.length && page.nextCursor && !page.items.some((item) => known.has(item.id))) {
      page = await getMessagePage(conversationId, signal, page.nextCursor)
      history = mergeMessages(page.items, history)
    }
    signal.throwIfAborted()
    if (!previous.length) olderCursor.value = page.nextCursor
    return mergeMessages(messages.value, history).map((message) => {
      const confirmed = confirmedFeedback.get(message.id)
      // A history read started before this PUT response cannot erase the confirmed value.
      return confirmed && confirmed.revision > requestedRevision
        ? { ...message, feedback: confirmed.value }
        : message
    })
  }
  async function loadOlderMessages(): Promise<void> {
    const id = selected.value?.id,
      cursor = olderCursor.value
    if (!id || !cursor || olderLoading.value || loading.value) return
    const ticket = olderScope.replace(),
      context = conversationScope.current()
    const signal = AbortSignal.any([ticket.signal, context.signal])
    olderLoading.value = true
    olderError.value = ''
    try {
      const page = await getMessagePage(id, signal, cursor)
      if (!ticket.isCurrent() || !context.isCurrent() || selected.value?.id !== id) return
      const element = messagesElement.value,
        height = element?.scrollHeight ?? 0,
        top = element?.scrollTop ?? 0
      messages.value = mergeMessages(page.items, messages.value)
      olderCursor.value = page.nextCursor
      await nextTick()
      if (element && ticket.isCurrent() && selected.value?.id === id)
        element.scrollTop = top + element.scrollHeight - height
    } catch (reason) {
      if (ticket.isCurrent() && context.isCurrent() && !isAborted(reason)) {
        olderError.value = errorMessage(reason)
        if (reason instanceof ApiError && [401, 403, 404].includes(reason.status)) {
          messages.value = []
          olderCursor.value = null
          historyReady.value = false
        }
      }
    } finally {
      if (ticket.isCurrent()) olderLoading.value = false
    }
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
      messages.value = await readMessages(selected.value.id, context.signal)
      if (context.isCurrent()) await scroll()
    }
  }
  async function acceptMainRun(
    run: RunSnapshot,
    context: ReturnType<RequestScope['current']>,
    clearComposer = true
  ): Promise<void> {
    const conversation = selected.value
    if (!conversation || run.resource_type !== 'chat' || run.resource_id !== conversation.id)
      throw new Error('对话运行与当前会话不匹配')
    if (activeRun.value?.id === run.id) updateRun(run)
    else {
      activeRun.value = null
      updateRun(run)
    }
    if (clearComposer) {
      pendingSend = null
      queue.clear()
      draft.value = ''
      selectedSkills.value = []
    }
    pendingRegenerations.delete(conversation.id)
    promoteConversation(conversation.id, run.created_at)
    messages.value = await readMessages(conversation.id, context.signal)
    if (!context.isCurrent()) return
    sending.value = false
    await scroll(clearComposer)
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
    context: ReturnType<RequestScope['current']>,
    clearComposer = preparationComposerPolicies.get(initial.id) ?? true
  ): Promise<void> {
    preparationComposerPolicies.set(initial.id, clearComposer)
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
    if (value.run) await acceptMainRun(value.run, context, clearComposer)
    else if (value.status === 'cancelled') error.value = '知识检索已取消'
    else error.value = value.error?.message ?? '知识检索未完成，请检查知识库后重试'
  }
  async function selectConversation(conversation: Conversation | null): Promise<void> {
    dismissInputIssue()
    const context = conversationScope.replace()
    messageActionScope.replace()
    feedbackBusy.clear()
    confirmedFeedback.clear()
    olderScope.replace()
    olderCursor.value = null
    olderLoading.value = false
    olderError.value = ''
    selected.value = conversation
    if (modelNeedsSelection) modelId.value = modelOptions.value[0]?.value
    modelNeedsSelection = false
    generationSettings.value = defaultGenerationSettings()
    queue.clear()
    messages.value = []
    activeRun.value = null
    activePreparation.value = null
    error.value = ''
    draft.value = ''
    selectedSkills.value = []
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
      const history = await readMessages(conversation.id, context.signal)
      if (!context.isCurrent()) return
      messages.value = history
      const config = history.at(-1)?.generation_config
      if (config) {
        generationSettings.value = {
          system_prompt: config.system_prompt,
          temperature: config.temperature,
          max_output_tokens: config.max_output_tokens
        }
        if (modelOptions.value.some((model) => model.value === config.model_id)) {
          modelId.value = config.model_id
        } else {
          modelId.value = undefined
          modelNeedsSelection = true
          error.value = '原模型当前不可用，请重新选择模型'
        }
      }
      const pending = pendingRegenerations.get(conversation.id)
      if (
        pending &&
        history.some(
          (message) =>
            message.role === 'assistant' &&
            message.generation_index > pending.generationIndex &&
            (pending.questionId === null || message.reply_to_message_id === pending.questionId)
        )
      )
        pendingRegenerations.delete(conversation.id)
      if (conversation.preparation_id) {
        pendingRegenerations.delete(conversation.id)
        historyReady.value = true
        const preparation = await getPreparation(conversation.preparation_id, context.signal)
        if (!context.isCurrent()) return
        historyReady.value = true
        loading.value = false
        await followKnowledge(preparation, context, false)
        return
      }
      const last = history.at(-1)
      if (last?.run_id && (last.role === 'user' || !last.is_complete)) {
        const run = await getRun(last.run_id, context.signal)
        if (!context.isCurrent()) return
        activeRun.value = run
        historyReady.value = true
        loading.value = false
        await follow(run, context)
      } else {
        historyReady.value = true
      }
      if (context.isCurrent()) await scroll(true)
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
    clearTimeout(searchTimer)
    const context = pageScope.replace()
    listMoreScope.replace()
    moreConversationsLoading.value = false
    listLoading.value = true
    listError.value = ''
    try {
      const [availableModels, page] = await Promise.all([
        getModels(context.signal),
        getConversationPage(context.signal, archivedFilter.value, historySearch.value.trim())
      ])
      if (!context.isCurrent()) return
      models.value = availableModels
      const items = page.items
      conversationCursor.value = page.nextCursor
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
      if (
        !modelNeedsSelection &&
        !modelOptions.value.some((model) => model.value === modelId.value)
      ) {
        modelId.value = modelOptions.value[0]?.value
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) listError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) listLoading.value = false
    }
  }
  function setHistorySearch(value: string): void {
    historySearch.value = value
    pageScope.replace()
    listMoreScope.replace()
    conversations.value = []
    conversationCursor.value = null
    moreConversationsLoading.value = false
    listLoading.value = true
    clearTimeout(searchTimer)
    searchTimer = setTimeout(() => void load(), 250)
  }
  async function loadMoreConversations(): Promise<void> {
    const cursor = conversationCursor.value
    if (!cursor || listLoading.value || moreConversationsLoading.value) return
    const ticket = listMoreScope.replace(),
      context = pageScope.current()
    moreConversationsLoading.value = true
    listError.value = ''
    try {
      const page = await getConversationPage(
        AbortSignal.any([ticket.signal, context.signal]),
        archivedFilter.value,
        historySearch.value.trim(),
        cursor
      )
      if (!ticket.isCurrent() || !context.isCurrent()) return
      const ids = new Set(conversations.value.map((item) => item.id))
      conversations.value.push(...page.items.filter((item) => !ids.has(item.id)))
      conversationCursor.value = page.nextCursor
    } catch (reason) {
      if (ticket.isCurrent() && context.isCurrent() && !isAborted(reason))
        listError.value = errorMessage(reason)
    } finally {
      if (ticket.isCurrent()) moreConversationsLoading.value = false
    }
  }
  function promoteConversation(id: string, activity: string): void {
    const current = conversations.value.find((item) => item.id === id) ?? selected.value
    if (!current || current.id !== id || current.archived !== archivedFilter.value) return
    if (!current.title.toLocaleLowerCase().includes(historySearch.value.trim().toLocaleLowerCase()))
      return
    conversations.value = [
      { ...current, last_activity_at: activity },
      ...conversations.value.filter((item) => item.id !== id)
    ]
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
        const history = await readMessages(conversationId, context.signal)
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
    (name) => {
      if (name === 'chat') void load()
    },
    { immediate: true }
  )
  async function send(retry = false): Promise<void> {
    const model = modelId.value
    const message = draft.value.trim() || (queuedFiles.value.length ? '请分析上传的文件。' : '')
    if (
      sending.value ||
      regenerationPending.value ||
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
    if (!retry) {
      const settingsError = generationSettingsError(
        generationSettings.value,
        selectedModel.value?.max_output_tokens
      )
      if (settingsError) {
        error.value = settingsError
        return
      }
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
              ...generationRequest(generationSettings.value),
              message,
              skill_slugs: selectedSkills.value.map((skill) => skill.skillSlug),
              file_ids: queuedFiles.value.map((file) => file.fileId!)
            },
            createRequestId
          )
        : null
    if (!request) return
    pendingSend = request
    sending.value = true
    skillPickerOpen.value = false
    error.value = ''
    dismissInputIssue()
    canRetry.value = false
    try {
      let conversationId = request.conversationId
      if (conversationId === null) {
        const conversation = await createConversation(context.signal)
        if (!isCurrent()) return
        pageScope.replace()
        listLoading.value = false
        selected.value = conversation
        if (
          !archivedFilter.value &&
          conversation.title
            .toLocaleLowerCase()
            .includes(historySearch.value.trim().toLocaleLowerCase())
        )
          conversations.value.unshift(conversation)
        conversationId = conversation.id
        pendingSend = { ...request, conversationId }
      }
      const admission = await sendMessage(
        conversationId,
        {
          model_id: request.model_id,
          ...generationRequest(request),
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
        const issue = inputIssueFrom(reason)
        if (issue) {
          inputIssue.value = issue
          pendingSend = null
          canRetry.value = false
          error.value = ''
        } else {
          error.value = errorMessage(reason)
          canRetry.value = pendingSend !== null
        }
        disconnected.value = running.value
      }
    } finally {
      if (isCurrent()) sending.value = false
    }
  }
  function messageActionReason(message: ChatMessage): string {
    if (loading.value || !historyReady.value) return '请等待会话加载完成'
    if (message.run_id === activeRun.value?.id && running.value)
      return '本次回复正在生成，结束后可操作'
    if (!messages.value.some((item) => item.id === message.id)) return '请刷新会话后重试'
    return ''
  }
  function shareReason(message: ChatMessage): string {
    return (
      messageActionReason(message) || (message.content.trim() ? '' : '当前回复没有可分享的正文')
    )
  }
  function regenerationReason(message: ChatMessage): string {
    if (selected.value?.archived) return '会话已归档，取消归档后可重新生成'
    if (readOnly.value) return '会话状态待核对，请刷新会话'
    if (running.value || sending.value) return '当前正在生成或提交，请结束后重试'
    if (loading.value || !historyReady.value) return '请等待会话加载完成'
    if (canRetry.value) return '上一条发送结果待确认，请先使用原请求重试'
    const pending = selected.value ? pendingRegenerations.get(selected.value.id) : undefined
    if (pending && pending.messageId !== message.id)
      return '重新生成结果待确认，请使用原重新生成请求重试'
    const row = groupMessageVersions(messages.value).find((row) =>
      row.versions.some((item) => item.id === message.id)
    )
    const latestQuestion = [...messages.value].reverse().find((item) => item.role === 'user')
    if (!latestQuestion || row?.questionId !== latestQuestion.id) return '只能重新生成最新一轮回复'
    return messages.value.some((item) => item.id === message.id) ? '' : '请刷新会话后重试'
  }
  async function regenerate(message: ChatMessage): Promise<void> {
    const blocked = regenerationReason(message)
    if (blocked) {
      error.value = blocked
      return
    }
    const conversation = selected.value
    const owner = identity.value?.id
    if (!conversation || !owner) return
    const versions = groupMessageVersions(messages.value).find((row) =>
      row.versions.some((item) => item.id === message.id)
    )
    const highestGeneration = Math.max(
      ...(versions?.versions ?? [message]).map((item) => item.generation_index)
    )
    const intent = nextRegenerationIntent(
      pendingRegenerations.get(conversation.id) ?? null,
      conversation.id,
      {
        ...message,
        reply_to_message_id: versions?.questionId ?? message.reply_to_message_id,
        generation_index: highestGeneration
      },
      createRequestId
    )
    pendingRegenerations.set(conversation.id, intent)
    const context = conversationScope.replace()
    const isCurrent = () => context.isCurrent() && identity.value?.id === owner
    sending.value = true
    error.value = ''
    try {
      const admission = await regenerateMessage(
        conversation.id,
        intent.messageId,
        intent.clientRequestId,
        context.signal
      )
      if (!isCurrent()) return
      pendingRegenerations.delete(conversation.id)
      if (isPreparation(admission)) {
        if (activeRun.value?.id !== admission.run?.id) activeRun.value = null
        await followKnowledge(admission, context, false)
      } else await acceptMainRun(admission, context, false)
    } catch (reason) {
      if (isCurrent() && !isAborted(reason)) {
        if (reason instanceof ApiError && reason.status < 500)
          pendingRegenerations.delete(conversation.id)
        error.value = `${errorMessage(reason)}${pendingRegenerations.has(conversation.id) ? '；重新生成结果待确认，请使用原请求重试。' : ''}`
        disconnected.value = running.value
      }
    } finally {
      if (isCurrent()) sending.value = false
    }
  }
  async function retryRegeneration(): Promise<void> {
    const pending = selected.value ? pendingRegenerations.get(selected.value.id) : undefined
    const message = pending
      ? messages.value.find((item) => item.id === pending.messageId)
      : undefined
    if (message) await regenerate(message)
  }
  async function feedback(message: ChatMessage, value: FeedbackValue): Promise<void> {
    if (feedbackBusy.has(message.id)) return
    const blocked = messageActionReason(message)
    if (blocked) {
      error.value = blocked
      return
    }
    const context = messageActionScope.current(),
      conversationId = selected.value?.id,
      owner = identity.value?.id
    feedbackBusy.add(message.id)
    try {
      const result = await setMessageFeedback(message.id, value, context.signal)
      if (
        !context.isCurrent() ||
        selected.value?.id !== conversationId ||
        identity.value?.id !== owner
      )
        return
      if (result.message_id !== message.id) throw new Error('消息反馈与当前回复不匹配')
      confirmedFeedback.set(message.id, { revision: ++feedbackRevision, value: result.value })
      const current = messages.value.find((item) => item.id === message.id)
      if (current) current.feedback = result.value
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) feedbackBusy.delete(message.id)
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
    conversations.value = []
    conversationCursor.value = null
    void load()
  }
  function conversationLocked(id: string): boolean {
    return (
      metadataBusy.value.length > 0 ||
      metadataUnknown.value.includes(id) ||
      pendingStops.has(id) ||
      Boolean(conversations.value.find((item) => item.id === id)?.preparation_id) ||
      (selected.value?.id === id && (sending.value || running.value || canRetry.value)) ||
      pendingRegenerations.has(id)
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
        result.last_activity_at = target.last_activity_at ?? target.created_at
        conversations.value = conversations.value.filter((item) => item.id !== target.id)
        if (
          result.archived === archivedFilter.value &&
          result.title.toLocaleLowerCase().includes(historySearch.value.trim().toLocaleLowerCase())
        ) {
          conversations.value.push(result)
          conversations.value.sort(
            (a, b) =>
              Date.parse(b.last_activity_at ?? b.created_at) -
                Date.parse(a.last_activity_at ?? a.created_at) || b.id.localeCompare(a.id)
          )
        }
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
  function applyGenerationSettings(settings: ChatGenerationSettings): void {
    if (sending.value || running.value || loading.value || readOnly.value) return
    const settingsError = generationSettingsError(settings, selectedModel.value?.max_output_tokens)
    if (settingsError) {
      error.value = settingsError
      return
    }
    generationSettings.value = { ...settings }
  }
  function setModelId(value: unknown): void {
    modelId.value = typeof value === 'string' ? value : undefined
    if (modelId.value) modelNeedsSelection = false
    if (modelId.value && error.value === '原模型当前不可用，请重新选择模型') error.value = ''
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
    ...contextObservation,
    historySearch,
    setHistorySearch,
    conversationCursor,
    moreConversationsLoading,
    loadMoreConversations,
    olderCursor,
    olderLoading,
    olderError,
    loadOlderMessages,
    regenerate,
    retryRegeneration,
    regenerationPending,
    canRetryRegeneration,
    regenerationReason,
    messageActionReason,
    shareReason,
    feedback,
    feedbackBusy,
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
    selectedModel,
    generationSettings,
    applyGenerationSettings,
    messages,
    draft,
    selectedSkills,
    skillPickerOpen,
    removeSkill,
    handleSkillSelected,
    error,
    inputIssue,
    dismissInputIssue,
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
    search: readonly(state.historySearch),
    setSearch: state.setHistorySearch,
    more: readonly(state.conversationCursor),
    moreLoading: readonly(state.moreConversationsLoading),
    loadMore: state.loadMoreConversations,
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
