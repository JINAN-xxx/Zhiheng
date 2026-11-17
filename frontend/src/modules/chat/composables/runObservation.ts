import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import type { Ref } from 'vue'
import { errorMessage, isAborted } from '../../../shared/http.ts'
import type { ContextTicket, RequestScope } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { cancelRun, getRun, observeRun } from '../api/index.ts'
import type { Conversation } from '../api/index.ts'

interface RunObservationOptions {
  selected: Ref<Conversation | null>
  conversationScope: RequestScope
  error: Ref<string>
  scroll: (force?: boolean) => Promise<void>
  refreshHistory: (context: ContextTicket) => Promise<void>
}

export function useChatRunObservation(options: RunObservationOptions) {
  const { selected, conversationScope, error, scroll, refreshHistory } = options
  const pendingStops = reactive(new Set<string>())

  const stopOperations = new Map<string, Promise<void>>()

  const stopping = ref(false)

  const disconnected = ref(false)

  const activeRun = ref<RunSnapshot | null>(null)

  const stopAcknowledged = computed(() => activeRun.value?.status === 'cancel_requested')

  const running = computed(
    () =>
      Boolean(selected.value && pendingStops.has(selected.value.id)) ||
      (activeRun.value !== null && !isTerminal(activeRun.value))
  )

  const canRecover = computed(() => disconnected.value && running.value)

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
    if (context.isCurrent() && selected.value) await refreshHistory(context)
  }

  function stop(): Promise<void> {
    const conversationId = selected.value?.id
    if (!conversationId) return Promise.resolve()
    const existing = stopOperations.get(conversationId)
    if (existing) return existing
    const run = activeRun.value
    if (stopAcknowledged.value) return Promise.resolve()
    if (!run || isTerminal(run)) {
      pendingStops.delete(conversationId)
      return Promise.resolve()
    }
    pendingStops.add(conversationId)
    const context = conversationScope.current()
    stopping.value = true
    const operation = (async () => {
      try {
        const result = await cancelRun(run.id, context.signal)
        if (context.isCurrent()) updateRun(result)
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
  function reset(): void {
    activeRun.value = null
    disconnected.value = false
    stopping.value = false
  }
  onBeforeUnmount(() => {
    pendingStops.clear()
    stopOperations.clear()
  })
  return {
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
    reconnect,
    reset
  }
}
