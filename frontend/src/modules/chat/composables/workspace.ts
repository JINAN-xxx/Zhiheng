import { computed, inject, nextTick, onBeforeUnmount, provide, readonly, ref, watch } from 'vue'
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
  const messagesElement = ref<HTMLElement | null>(null)
  const pageScope = new RequestScope()
  const conversationScope = new RequestScope()
  const running = computed(() => activeRun.value !== null && !isTerminal(activeRun.value))
  const canRecover = computed(() => disconnected.value && running.value)
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
    skillPickerOpen.value = false
  })
  async function scroll(): Promise<void> {
    await nextTick()
    messagesElement.value?.scrollTo({ top: messagesElement.value.scrollHeight, behavior: 'smooth' })
  }
  function updateRun(run: RunSnapshot): void {
    if (acceptSnapshot(activeRun.value, run)) {
      activeRun.value = run
      void scroll()
    }
  }
  async function follow(
    run: RunSnapshot,
    context: ReturnType<RequestScope['current']>
  ): Promise<void> {
    disconnected.value = false
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
  async function selectConversation(conversation: Conversation | null): Promise<void> {
    const context = conversationScope.replace()
    selected.value = conversation
    queue.clear()
    messages.value = []
    activeRun.value = null
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
      const history = await getMessages(conversation.id, context.signal)
      if (!context.isCurrent()) return
      messages.value = history
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
      if (!modelOptions.value.some((model) => model.value === modelId.value)) {
        modelId.value = modelOptions.value[0]?.value
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) listError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) listLoading.value = false
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
              file_ids: queuedFiles.value.map((file) => file.fileId!)
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
      const run = await sendMessage(
        conversationId,
        {
          model_id: request.model_id,
          message: request.message,
          client_request_id: request.client_request_id,
          skill_slugs: request.skill_slugs ?? [],
          file_ids: request.file_ids ?? []
        },
        context.signal
      )
      if (!isCurrent()) return
      activeRun.value = run
      pendingSend = null
      queue.clear()
      draft.value = ''
      selectedSkills.value = []
      messages.value = await getMessages(conversationId, context.signal)
      if (!isCurrent()) return
      sending.value = false
      await scroll()
      await follow(run, context)
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
  async function stop(): Promise<void> {
    const run = activeRun.value
    if (!run || stopping.value) return
    const context = conversationScope.current()
    stopping.value = true
    try {
      const result = await cancelRun(run.id, context.signal)
      if (context.isCurrent()) updateRun(result)
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) stopping.value = false
    }
  }
  async function reconnect(): Promise<void> {
    const run = activeRun.value
    if (!run) return
    const context = conversationScope.replace()
    error.value = ''
    try {
      const latest = await getRun(run.id, context.signal)
      if (!context.isCurrent()) return
      updateRun(latest)
      await follow(latest, context)
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
