import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { identity } from '../../identity/public.ts'
import { getContextInfo, retryContext } from '../api/index.ts'
import type { Conversation } from '../api/index.ts'
import { contextDescription, nextContextRetryIntent } from '../contextInfo.ts'
import type { ChatContextInfo, ContextRetryIntent } from '../contextInfo.ts'

export function useChatContextObservation(
  selected: Ref<Conversation | null>,
  activeRun: Ref<RunSnapshot | null>
) {
  const contextInfo = ref<ChatContextInfo | null>(null)
  const contextError = ref('')
  const contextRetrying = ref(false)
  const contextNotice = computed(
    () => contextError.value || (contextInfo.value ? contextDescription(contextInfo.value) : '')
  )
  const scope = new RequestScope()
  const intents = new Map<string, ContextRetryIntent>()
  let timer: ReturnType<typeof setTimeout> | undefined
  let refreshVersion = 0
  function clearTimer(): void {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }
  function schedule(delay: number): void {
    clearTimer()
    if (document.visibilityState !== 'hidden') timer = setTimeout(() => void refresh(), delay)
  }
  async function refresh(): Promise<void> {
    clearTimer()
    const conversationId = selected.value?.id
    const ownerId = identity.value?.id
    if (!conversationId || !ownerId || document.visibilityState === 'hidden') return
    const ticket = scope.current()
    const version = ++refreshVersion
    try {
      const info = await getContextInfo(conversationId, ticket.signal)
      if (
        !ticket.isCurrent() ||
        version !== refreshVersion ||
        selected.value?.id !== conversationId ||
        identity.value?.id !== ownerId
      )
        return
      contextInfo.value = info
      contextError.value = ''
      if (info.dirty || info.status === 'summarizing') schedule(1500)
    } catch (error) {
      if (ticket.isCurrent() && version === refreshVersion && !isAborted(error)) {
        contextError.value = '会话整理状态暂不可用，可继续聊天。'
        schedule(5000)
      }
    }
  }
  async function retryChatContext(): Promise<void> {
    const conversationId = selected.value?.id
    const ownerId = identity.value?.id
    if (!conversationId || !ownerId || selected.value?.archived || contextRetrying.value) return
    const key = `${ownerId}:${conversationId}`
    const intent = nextContextRetryIntent(intents.get(key) ?? null, ownerId, conversationId)
    intents.set(key, intent)
    const ticket = scope.current()
    contextRetrying.value = true
    contextError.value = ''
    try {
      const info = await retryContext(conversationId, intent.requestId, ticket.signal)
      if (!ticket.isCurrent()) return
      intents.delete(key)
      contextInfo.value = info
      if (info.dirty || info.status === 'summarizing') schedule(1500)
    } catch (error) {
      if (!ticket.isCurrent() || isAborted(error)) return
      if (error instanceof ApiError && error.status < 500) {
        intents.delete(key)
        contextError.value = errorMessage(error)
      } else contextError.value = '尚未确认摘要重试结果，再次点击会沿用原请求。'
    } finally {
      if (ticket.isCurrent()) contextRetrying.value = false
    }
  }
  watch(
    () => [selected.value?.id, identity.value?.id],
    ([, owner], [, previousOwner]) => {
      scope.replace()
      clearTimer()
      contextInfo.value = null
      contextError.value = ''
      contextRetrying.value = false
      if (owner !== previousOwner) intents.clear()
      void refresh()
    }
  )
  watch(
    () => (activeRun.value ? isTerminal(activeRun.value) : false),
    (terminal) => {
      if (terminal && activeRun.value?.resource_id === selected.value?.id) void refresh()
    }
  )
  function visibilityChanged(): void {
    scope.replace()
    contextRetrying.value = false
    clearTimer()
    if (document.visibilityState !== 'hidden') void refresh()
  }
  onMounted(() => document.addEventListener('visibilitychange', visibilityChanged))
  onBeforeUnmount(() => {
    scope.dispose()
    clearTimer()
    intents.clear()
    document.removeEventListener('visibilitychange', visibilityChanged)
  })
  return { contextInfo, contextNotice, contextRetrying, retryChatContext }
}
