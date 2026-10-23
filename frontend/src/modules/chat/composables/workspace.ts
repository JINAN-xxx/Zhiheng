import { computed, inject, nextTick, onBeforeUnmount, provide, readonly, ref, watch } from 'vue'
import type { InjectionKey } from 'vue'
import { useRoute } from 'vue-router'

import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
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
  getMessages,
  getRun,
  observeRun,
  sendMessage
} from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { nextSendIntent } from '../api/sendIntent.ts'
import type { SendIntent } from '../api/sendIntent.ts'

function createChatWorkspace() {
  const conversations = ref<Conversation[]>([])
  const models = ref<ChatModel[]>([])
  const selected = ref<Conversation | null>(null)
  const modelId = ref<string>()
  const messages = ref<ChatMessage[]>([])
  const draft = ref('')
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
        getConversations(context.signal)
      ])
      if (!context.isCurrent()) return
      models.value = availableModels
      conversations.value = items
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
    const message = draft.value.trim()
    if (
      sending.value ||
      running.value ||
      loading.value ||
      listLoading.value ||
      !historyReady.value ||
      (!retry && (!message || !model))
    )
      return
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
              skill_slugs: selectedSkills.value.map((skill) => skill.skillSlug)
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
          skill_slugs: request.skill_slugs ?? []
        },
        context.signal
      )
      if (!isCurrent()) return
      activeRun.value = run
      pendingSend = null
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
    refresh: state.load,
    select: state.selectConversation
  }
}
