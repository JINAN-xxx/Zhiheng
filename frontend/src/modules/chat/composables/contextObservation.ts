import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { ApiError, isAborted, RequestScope } from '../../../shared/http.ts'
import { isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { identity } from '../../identity/public.ts'
import { getContextInfo } from '../api/index.ts'
import type { Conversation } from '../api/index.ts'
import { contextDescription } from '../contextInfo.ts'
import type { ChatContextInfo } from '../contextInfo.ts'

export function useChatContextObservation(
  selected: Ref<Conversation | null>,
  activeRun: Ref<RunSnapshot | null>
) {
  const contextInfo = ref<ChatContextInfo | null>(null)
  const contextError = ref('')
  const contextNotice = computed(
    () => contextError.value || (contextInfo.value ? contextDescription(contextInfo.value) : '')
  )
  const scope = new RequestScope()
  let timer: ReturnType<typeof setTimeout> | undefined
  let refreshVersion = 0
  let paused = false
  let retryDelay = 1500
  const pageWindow = document.defaultView
  function resetFailure(): void {
    paused = false
    retryDelay = 1500
    contextError.value = ''
  }
  function clearTimer(): void {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }
  function schedule(delay: number): void {
    clearTimer()
    if (document.visibilityState !== 'hidden') timer = setTimeout(() => void refresh(), delay)
  }
  function hasActiveRun(): boolean {
    const run = activeRun.value
    return Boolean(run && run.resource_id === selected.value?.id && !isTerminal(run))
  }
  async function refresh(): Promise<void> {
    clearTimer()
    const conversationId = selected.value?.id
    const ownerId = identity.value?.id
    if (paused || !conversationId || !ownerId || document.visibilityState === 'hidden') return
    const ticket = scope.current()
    const version = ++refreshVersion
    const current = () =>
      ticket.isCurrent() &&
      version === refreshVersion &&
      selected.value?.id === conversationId &&
      identity.value?.id === ownerId
    try {
      const info = await getContextInfo(conversationId, ticket.signal)
      if (!current()) return
      contextInfo.value = info
      resetFailure()
      if (info.status === 'summarizing' || hasActiveRun()) schedule(1500)
    } catch (error) {
      if (!current() || isAborted(error)) return
      contextInfo.value = null
      if (
        error instanceof TypeError ||
        (error instanceof ApiError && (error.status === 429 || error.status >= 500))
      ) {
        contextError.value = '会话整理状态暂时无法读取，正在重试。'
        schedule(retryDelay)
        retryDelay = Math.min(retryDelay * 2, 15000)
        return
      }
      paused = true
      contextError.value =
        error instanceof ApiError
          ? error.status === 401
            ? ''
            : error.status === 403
              ? '当前账号无权查看此会话的整理状态。'
              : error.status === 404
                ? '此会话不存在或当前不可访问。'
                : '无法读取此会话的整理状态，请刷新页面后核对。'
          : '会话整理状态数据异常，请刷新页面后重试。'
    }
  }
  watch([() => selected.value?.id, () => identity.value?.id], () => {
    scope.replace()
    clearTimer()
    contextInfo.value = null
    resetFailure()
    void refresh()
  })
  watch([() => activeRun.value?.id, () => activeRun.value?.status], ([id], [previousId]) => {
    if (id && id !== previousId) resetFailure()
    if (activeRun.value?.resource_id === selected.value?.id) void refresh()
    else clearTimer()
  })
  function pageFocused(): void {
    if (document.visibilityState === 'hidden') return
    scope.replace()
    clearTimer()
    resetFailure()
    void refresh()
  }
  function visibilityChanged(): void {
    scope.replace()
    clearTimer()
    if (document.visibilityState !== 'hidden') {
      resetFailure()
      void refresh()
    }
  }
  onMounted(() => {
    document.addEventListener('visibilitychange', visibilityChanged)
    pageWindow?.addEventListener('focus', pageFocused)
  })
  onBeforeUnmount(() => {
    scope.dispose()
    clearTimer()
    document.removeEventListener('visibilitychange', visibilityChanged)
    pageWindow?.removeEventListener('focus', pageFocused)
  })
  return { contextInfo, contextNotice }
}
