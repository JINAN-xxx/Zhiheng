import { onBeforeUnmount, ref, watch } from 'vue'
import { canUseModule, identity } from '../../identity/public.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { downloadArtifact, downloadInput } from '../api/index.ts'
import type { WorkFile } from '../artifacts.ts'

/** Every preview/download belongs to one task and identity, including blob decoding. */
export function useWorkFileAccess(taskId: () => string) {
  const pending = ref(''),
    error = ref('')
  const scope = new RequestScope()
  function reset(): void {
    scope.replace()
    pending.value = ''
    error.value = ''
  }
  async function read<T>(
    file: WorkFile,
    preview: boolean,
    decode: (blob: Blob) => T | Promise<T>
  ): Promise<T | null> {
    const ticket = scope.replace(),
      task = taskId(),
      owner = identity.value?.id
    if (!owner || !canUseModule('work')) return null
    pending.value = file.id
    error.value = ''
    try {
      const blob =
        file.kind === 'input'
          ? await downloadInput(task, file.fileId, ticket.signal)
          : await downloadArtifact(task, file.id, ticket.signal, preview)
      const result = await decode(blob)
      return ticket.isCurrent() &&
        taskId() === task &&
        identity.value?.id === owner &&
        canUseModule('work')
        ? result
        : null
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
      return null
    } finally {
      if (ticket.isCurrent()) pending.value = ''
    }
  }
  watch([taskId, () => identity.value?.id, () => canUseModule('work')], reset, { flush: 'sync' })
  onBeforeUnmount(() => scope.dispose())
  return { pending, error, read, reset }
}
