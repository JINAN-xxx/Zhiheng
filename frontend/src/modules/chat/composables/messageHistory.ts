import { nextTick, onBeforeUnmount, reactive, ref } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { canUseCapability, identity } from '../../identity/public.ts'
import { getMessagePage } from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { setMessageFeedback } from '../api/messageActions.ts'
import type { FeedbackValue } from '../api/messageActions.ts'

interface MessageHistoryOptions {
  selected: Ref<Conversation | null>
  conversationScope: RequestScope
  messagesElement: Ref<HTMLElement | null>
  loading: Ref<boolean>
  historyReady: Ref<boolean>
  error: Ref<string>
  messageActionReason: (message: ChatMessage) => string
}

export function useChatMessageHistory(options: MessageHistoryOptions) {
  const {
    selected,
    conversationScope,
    messagesElement,
    loading,
    historyReady,
    error,
    messageActionReason
  } = options
  const olderScope = new RequestScope()
  const olderCursor = ref<string | null>(null),
    olderLoading = ref(false),
    olderError = ref('')

  const messages = ref<ChatMessage[]>([])

  const messageActionScope = new RequestScope()

  const feedbackBusy = reactive(new Set<string>())

  const confirmedFeedback = new Map<string, { revision: number; value: FeedbackValue }>()

  let feedbackRevision = 0

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

  async function feedback(message: ChatMessage, value: FeedbackValue): Promise<void> {
    if (!canUseCapability('chat', 'feedback') || feedbackBusy.has(message.id)) return
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
  function reset(): void {
    messageActionScope.replace()
    feedbackBusy.clear()
    confirmedFeedback.clear()
    olderScope.replace()
    olderCursor.value = null
    olderLoading.value = false
    olderError.value = ''
    messages.value = []
  }
  onBeforeUnmount(() => {
    olderScope.dispose()
    messageActionScope.dispose()
    feedbackBusy.clear()
    confirmedFeedback.clear()
  })
  return {
    messages,
    olderCursor,
    olderLoading,
    olderError,
    feedbackBusy,
    readMessages,
    loadOlderMessages,
    feedback,
    reset
  }
}
