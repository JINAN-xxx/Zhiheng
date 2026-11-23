import { onBeforeUnmount, ref, watch } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../http.ts'

/** Bind async reads and their results to the conversation and current account. */
export function useMessageFileRequest(accessKey: () => string | null) {
  const scope = new RequestScope()
  const pending = ref(''),
    error = ref('')
  function reset(): void {
    scope.replace()
    pending.value = ''
    error.value = ''
  }
  async function read<T>(id: string, load: (signal: AbortSignal) => Promise<T>): Promise<T | null> {
    if (!accessKey()) return null
    const ticket = scope.replace()
    pending.value = id
    error.value = ''
    try {
      const value = await load(ticket.signal)
      return ticket.isCurrent() ? value : null
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
      return null
    } finally {
      if (ticket.isCurrent()) pending.value = ''
    }
  }
  watch(accessKey, reset, { flush: 'sync' })
  onBeforeUnmount(() => scope.dispose())
  return { pending, error, reset, read }
}
