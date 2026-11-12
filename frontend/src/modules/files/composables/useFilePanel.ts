import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import type { ManagedFile } from '../api/index.ts'
export function useFilePanel<T>(load: (id: string, signal: AbortSignal) => Promise<T>) {
  const visible = ref(false),
    loading = ref(false),
    error = ref('')
  const target = shallowRef<ManagedFile | null>(null),
    data = shallowRef<T | null>(null)
  const scope = new RequestScope()
  function close(): void {
    scope.replace()
    visible.value = false
    loading.value = false
    target.value = null
    data.value = null
    error.value = ''
  }
  async function open(file: ManagedFile): Promise<void> {
    const context = scope.replace()
    target.value = file
    data.value = null
    visible.value = true
    loading.value = true
    error.value = ''
    try {
      const value = await load(file.fileId, context.signal)
      if (context.isCurrent() && visible.value && target.value?.fileId === file.fileId)
        data.value = value
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
  onBeforeUnmount(() => {
    scope.dispose()
    target.value = null
    data.value = null
  })
  return { visible, loading, error, target, data, close, open }
}
