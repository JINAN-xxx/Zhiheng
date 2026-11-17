import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ContextTicket } from '../../../shared/http.ts'
import { getPreparation } from '../../knowledge/public.ts'
import { identity, canUseCapability } from '../../identity/public.ts'
import { getAgent } from '../api/index.ts'
import { message as toast } from 'ant-design-vue'
import { createAttachmentQueue } from '../../../shared/attachmentQueue.ts'
import type { QueuedFile } from '../../../shared/attachmentQueue.ts'
import { uploadAssistantAttachment } from '../api/conversations.ts'
import type { Agent } from '../api/index.ts'
import { getAssistantConversation, getAssistantConversations } from '../api/conversations.ts'
import type { AssistantConversation, AssistantTurn } from '../api/conversations.ts'
import { useAssistantCommands } from './useAssistantCommands.ts'
import type { AssistantOutgoingTurn } from './useAssistantCommands.ts'
import { useAssistantRunObservation } from './useAssistantRunObservation.ts'

export function useAssistantWorkspace() {
  const route = useRoute(),
    router = useRouter()
  const agentId = String(route.params.id)
  const agent = ref<Agent | null>(null)
  const conversations = ref<AssistantConversation[]>([])
  const selected = ref<AssistantConversation | null>(null)
  const turns = ref<AssistantTurn[]>([])
  const outgoing = ref<AssistantOutgoingTurn | null>(null)
  const older = ref<number | null>(null)
  const hasMore = ref(false)
  const historyOpen = ref(false),
    listLoading = ref(false),
    olderLoading = ref(false)
  const error = ref(''),
    listError = ref(''),
    loading = ref(false)
  const history = ref<HTMLElement | null>(null)
  const pageScope = new RequestScope(),
    conversationScope = new RequestScope()
  let identityController: AbortController | null = null
  let skipConversation = ''

  const runs = useAssistantRunObservation({
    selected,
    turns,
    outgoing,
    conversationScope,
    error,
    readDetail,
    refreshList,
    scroll
  })
  const queuedFiles = ref<QueuedFile[]>([])
  const fileQueue = createAttachmentQueue({
    upload: (file, signal) => uploadAssistantAttachment(agentId, file, signal),
    onChange: (files) => {
      queuedFiles.value = files
    }
  })
  const fileQueueReady = computed(() =>
    queuedFiles.value.every((file) => file.status === 'ready' && file.fileId)
  )
  function addFiles(files: Iterable<File>): void {
    if (!canSend.value || commands.pending.value) return
    const result = fileQueue.add(files, true)
    if (result.rejected.length)
      void toast.warning([...new Set(result.rejected.map((file) => file.error))].join('；'))
  }
  function removeFile(id: string): void {
    if (canSend.value && !commands.pending.value) fileQueue.remove(id)
  }
  function retryFile(id: string): void {
    if (canSend.value && !commands.pending.value) void fileQueue.retry(id)
  }
  const commands = useAssistantCommands({
    queuedFiles,
    clearFiles: () => fileQueue.clear(),
    agentId,
    selected,
    turns,
    outgoing,
    loading,
    error,
    conversationScope,
    runs,
    canSend: () => canSend.value,
    conversationId,
    openPendingConversation,
    conversationCreated,
    readDetail,
    refreshList,
    scroll
  })
  const configuration = computed(() => (agent.value?.status === 'enabled' ? agent.value : null))
  const name = computed(
    () => selected.value?.name ?? configuration.value?.name ?? agent.value?.name ?? '智能助手'
  )
  const welcome = computed(
    () => selected.value?.welcomeMessage ?? configuration.value?.welcomeMessage ?? ''
  )
  const canSend = computed(
    () =>
      canUseCapability('agents', 'use') &&
      agent.value?.status === 'enabled' &&
      selected.value?.memoryAvailable !== false &&
      !commands.recoveryBlocked.value &&
      (!route.query.conversation || selected.value?.id === route.query.conversation) &&
      !loading.value &&
      !commands.sending.value &&
      !runs.running.value
  )

  function conversationId(): string {
    return typeof route.query.conversation === 'string' ? route.query.conversation : ''
  }
  async function scroll(): Promise<void> {
    await nextTick()
    history.value?.scrollTo({ top: history.value.scrollHeight })
  }
  async function refreshList(append = false): Promise<void> {
    const context = pageScope.replace()
    listError.value = ''
    listLoading.value = true
    try {
      const cursor = append ? conversations.value.at(-1)?.id : undefined
      const rows = await getAssistantConversations(agentId, context.signal, cursor)
      if (!context.isCurrent()) return
      conversations.value = append ? [...conversations.value, ...rows] : rows
      hasMore.value = rows.length === 50
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) listError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) listLoading.value = false
    }
  }
  async function readDetail(
    id: string,
    context: ContextTicket
  ): Promise<AssistantConversation | undefined> {
    const result = await getAssistantConversation(agentId, id, context.signal)
    if (!context.isCurrent()) return
    selected.value = result.conversation
    older.value = result.nextBefore
    runs.restoreTurns(result.turns)
    commands.reconcileDetail(id, result)
    return result.conversation
  }
  async function loadConversation(id: string, context: ContextTicket): Promise<void> {
    loading.value = true
    try {
      const loaded = await readDetail(id, context)
      if (!context.isCurrent()) return
      await scroll()
      if (loaded?.preparationId) {
        const value = await getPreparation(loaded.preparationId, context.signal)
        if (context.isCurrent()) {
          loading.value = false
          await runs.followKnowledge(value, context)
        }
      } else {
        runs.preparation.value = null
        loading.value = false
        if (runs.activeRun.value) await runs.follow(runs.activeRun.value, context)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        runs.disconnected.value = runs.running.value
      }
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
  async function openConversation(id: string): Promise<void> {
    const context = conversationScope.replace()
    turns.value = []
    selected.value = null
    runs.reset()
    commands.resetConversation()
    older.value = null
    olderLoading.value = false
    error.value = ''
    loading.value = false
    if (id) await loadConversation(id, context)
  }
  function select(id?: string): void {
    void router.replace({
      name: 'assistant-chat',
      params: { id: agentId },
      query: id ? { conversation: id } : {}
    })
  }
  async function conversationCreated(id: string): Promise<void> {
    skipConversation = id
    await router.replace({
      name: 'assistant-chat',
      params: { id: agentId },
      query: { conversation: id }
    })
  }
  async function openPendingConversation(id: string): Promise<void> {
    await conversationCreated(id)
    await openConversation(id)
  }
  async function recover(): Promise<void> {
    const owner = conversationScope.current()
    await runs.waitForStop()
    if (!owner.isCurrent() || !selected.value) return
    const id = selected.value.id
    const context = conversationScope.replace()
    error.value = ''
    runs.disconnected.value = false
    await loadConversation(id, context)
  }
  async function loadOlder(): Promise<void> {
    if (!selected.value || older.value === null || olderLoading.value) return
    const context = conversationScope.current()
    olderLoading.value = true
    try {
      const value = await getAssistantConversation(
        agentId,
        selected.value.id,
        context.signal,
        older.value
      )
      if (context.isCurrent()) {
        turns.value = [...value.turns, ...turns.value]
        older.value = value.nextBefore
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) olderLoading.value = false
    }
  }
  function renamed(value: AssistantConversation): void {
    pageScope.replace()
    listLoading.value = false
    conversations.value = conversations.value.map((item) => (item.id === value.id ? value : item))
    if (selected.value?.id === value.id) selected.value = value
  }
  function removed(id: string): void {
    pageScope.replace()
    listLoading.value = false
    conversations.value = conversations.value.filter((item) => item.id !== id)
    if (selected.value?.id === id || route.query.conversation === id) {
      conversationScope.replace()
      turns.value = []
      selected.value = null
      runs.reset()
      commands.resetConversation()
      select()
    }
  }

  watch(
    () => route.query.conversation,
    () => {
      const id = conversationId()
      if (id && id === skipConversation) {
        skipConversation = ''
        return
      }
      void openConversation(id)
    },
    { immediate: true }
  )
  watch(
    () => identity.value?.id,
    async (owner) => {
      pageScope.replace()
      conversationScope.replace()
      historyOpen.value = false
      agent.value = null
      selected.value = null
      turns.value = []
      conversations.value = []
      runs.reset()
      commands.resetIdentity()
      error.value = ''
      listError.value = ''
      listLoading.value = false
      loading.value = false
      older.value = null
      olderLoading.value = false
      skipConversation = ''
      identityController?.abort()
      if (!owner) return
      commands.restorePending()
      const controller = new AbortController()
      identityController = controller
      try {
        const value = await getAgent(agentId, controller.signal)
        if (!controller.signal.aborted) agent.value = value
      } catch (reason) {
        if (!controller.signal.aborted && !isAborted(reason)) error.value = errorMessage(reason)
      }
      if (!controller.signal.aborted) {
        void refreshList()
        const id = conversationId()
        if (id) void openConversation(id)
      }
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    fileQueue.clear()
    pageScope.dispose()
    conversationScope.dispose()
    identityController?.abort()
    commands.resetIdentity()
    runs.reset()
  })
  return {
    queuedFiles,
    fileQueueReady,
    addFiles,
    removeFile,
    retryFile,
    agentId,
    agent,
    conversations,
    selected,
    turns,
    older,
    hasMore,
    historyOpen,
    listLoading,
    olderLoading,
    error,
    listError,
    loading,
    history,
    configuration,
    name,
    welcome,
    canSend,
    outgoing,
    draft: commands.draft,
    sending: commands.sending,
    pending: commands.pending,
    outgoingVisible: commands.outgoingVisible,
    outgoingRun: commands.outgoingRun,
    preparation: runs.preparation,
    disconnected: runs.disconnected,
    stopping: runs.stopping,
    stopAcknowledged: runs.stopAcknowledged,
    running: runs.running,
    preparationLabel: runs.preparationLabel,
    send: commands.send,
    stop: runs.stop,
    select,
    refreshList,
    loadOlder,
    recover,
    renamed,
    removed
  }
}
