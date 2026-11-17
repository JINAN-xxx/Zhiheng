import { useChatContextObservation } from './contextObservation.ts'
import { useChatComposer } from './composer.ts'
import { useChatCommands } from './commands.ts'
import { useChatMessageHistory } from './messageHistory.ts'
import { useChatRunObservation } from './runObservation.ts'
import { computed, inject, nextTick, onBeforeUnmount, provide, readonly, ref, watch } from 'vue'
import type { InjectionKey } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { identity, canUseModule, canUseCapability } from '../../identity/public.ts'
import { getModels } from '../../models/public.ts'
import {
  getConversationPage,
  getConversation,
  updateConversation,
  deleteConversation,
  getRun
} from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'

function createChatWorkspace() {
  const canUseChat = computed(() => canUseCapability('chat', 'use'))
  const conversations = ref<Conversation[]>([])

  const historySearch = ref(''),
    conversationCursor = ref<string | null>(null),
    moreConversationsLoading = ref(false)

  let searchTimer: ReturnType<typeof setTimeout> | undefined

  const selected = ref<Conversation | null>(null)

  const archivedFilter = ref(false),
    metadataBusy = ref<string[]>([]),
    metadataUnknown = ref<string[]>([])

  const metadataScope = new RequestScope()

  const readOnly = computed(
    () =>
      selected.value?.archived === true ||
      (selected.value !== null &&
        [...metadataBusy.value, ...metadataUnknown.value].includes(selected.value.id))
  )

  const error = ref('')

  const listError = ref('')

  const loading = ref(false)

  const listLoading = ref(false)

  const historyReady = ref(true)

  const messagesElement = ref<HTMLElement | null>(null)

  const pageScope = new RequestScope()

  const conversationScope = new RequestScope()
  const listMoreScope = new RequestScope()
  const composer = useChatComposer({
    readOnly,
    loading,
    error,
    isSending: () => commands.sending.value,
    isRunning: () => runs.running.value
  })
  const runs = useChatRunObservation({
    selected,
    conversationScope,
    error,
    scroll,
    refreshHistory: async (context) => {
      const id = selected.value?.id
      if (!id) return
      messages.value = await messageHistory.readMessages(id, context.signal)
      if (context.isCurrent()) await scroll()
    }
  })
  const messageHistory = useChatMessageHistory({
    selected,
    conversationScope,
    messagesElement,
    loading,
    historyReady,
    error,
    messageActionReason
  })
  const commands = useChatCommands({
    selected,
    conversationScope,
    error,
    readOnly,
    loading,
    listLoading,
    historyReady,
    messages: messageHistory.messages,
    composer,
    runs,
    onConversationCreated,
    acceptMainRun
  })
  const {
    models,
    modelId,
    selectedModel,
    modelOptions,
    generationSettings,
    draft,
    queuedFiles,
    supportsVision,
    fileQueueReady,
    selectedSkills,
    skillPickerOpen,
    addFiles,
    removeFile,
    retryFile,
    applyGenerationSettings,
    setModelId,
    removeSkill,
    handleSkillSelected
  } = composer
  const {
    messages,
    olderCursor,
    olderLoading,
    olderError,
    feedbackBusy,
    readMessages,
    loadOlderMessages,
    feedback
  } = messageHistory
  const {
    activeRun,
    stopping,
    disconnected,
    stopAcknowledged,
    running,
    canRecover,
    pendingStops,
    updateRun,
    follow,
    stop,
    reconnect
  } = runs
  const {
    sending,
    canRetry,
    inputIssue,
    dismissInputIssue,
    regenerationPending,
    canRetryRegeneration,
    send,
    regenerate,
    retryRegeneration,
    regenerationReason,
    keydown
  } = commands
  const contextObservation = useChatContextObservation(selected, activeRun)
  onBeforeUnmount(() => {
    clearTimeout(searchTimer)
    listMoreScope.dispose()
    metadataScope.dispose()
    pageScope.dispose()
    conversationScope.dispose()
  })
  const visibleMessages = computed(() =>
    messages.value.filter(
      (message) => !(message.role === 'assistant' && message.run_id === activeRun.value?.id)
    )
  )
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
    commands.accepted(conversation.id)
    if (clearComposer) composer.clear()
    promoteConversation(conversation.id, run.created_at)
    messages.value = await readMessages(conversation.id, context.signal)
    if (!context.isCurrent()) return
    sending.value = false
    await scroll(clearComposer)
    if (activeRun.value?.id === run.id) run = activeRun.value
    await follow(run, context)
  }

  async function selectConversation(conversation: Conversation | null): Promise<void> {
    dismissInputIssue()
    const context = conversationScope.replace()
    messageHistory.reset()
    selected.value = conversation
    composer.prepareSelection()
    composer.clear()
    runs.reset()
    error.value = ''
    commands.resetSelection()
    loading.value = false
    historyReady.value = conversation === null
    if (!conversation) return
    loading.value = true
    try {
      const history = await readMessages(conversation.id, context.signal)
      if (!context.isCurrent()) return
      messages.value = history
      const config = history.at(-1)?.generation_config
      if (config) composer.restoreGeneration(config)
      commands.reconcileHistory(conversation.id, history)
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
        canUseChat.value ? getModels(context.signal) : Promise.resolve([]),
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
      composer.ensureModelSelection()
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

  const route = useRoute()

  watch(
    () => route.name,
    (name) => {
      if (name === 'chat' && canUseModule('chat')) void load()
    },
    { immediate: true }
  )

  function messageActionReason(message: ChatMessage): string {
    if (loading.value || !historyReady.value) return '请等待会话加载完成'
    if (message.run_id === activeRun.value?.id && running.value)
      return '本次回复正在生成，结束后可操作'
    if (!messages.value.some((item) => item.id === message.id)) return '请刷新会话后重试'
    return ''
  }

  function shareReason(message: ChatMessage): string {
    if (!canUseCapability('chat', 'share')) return '当前账号没有分享权限'
    return (
      messageActionReason(message) || (message.content.trim() ? '' : '当前回复没有可分享的正文')
    )
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
      (selected.value?.id === id && (sending.value || running.value || canRetry.value)) ||
      commands.hasPendingRegeneration(id) ||
      commands.hasPendingSend(id)
    )
  }

  async function changeConversation(
    conversation: Conversation,
    change: { title?: string; archived?: boolean } | 'delete'
  ): Promise<boolean> {
    if (!canUseCapability('chat', change === 'delete' ? 'delete' : 'update')) return false
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
  function onConversationCreated(conversation: Conversation): void {
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
  }

  return {
    ...contextObservation,
    canUseChat,
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
