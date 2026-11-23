import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createConnection, updateConnection } from '../api/index.ts'
import type { ModelConnection } from '../api/index.ts'
import type { ModelAccess } from './useModelAccess.ts'

interface ConnectionConfigurationOptions {
  access: ModelAccess
  saved: (connection: ModelConnection, editing: boolean) => void
}
export function useConnectionConfiguration(options: ConnectionConfigurationOptions) {
  const { canCreateCredentials, canUpdateCredentials } = options.access
  const connectionOpen = ref(false)
  const editingConnection = ref<ModelConnection | null>(null)
  const connectionForm = reactive({ name: '', base_url: '', api_key: '' })
  const requiresKey = computed(
    () =>
      !editingConnection.value ||
      connectionForm.base_url.trim().replace(/\/+$/, '') !==
        editingConnection.value.base_url.replace(/\/+$/, '')
  )
  const saving = ref(false)
  const modalError = ref('')
  const mutationScope = new RequestScope()
  function openConnection(connection: ModelConnection | null = null): void {
    if (!(connection ? canUpdateCredentials.value : canCreateCredentials.value)) return
    mutationScope.replace()
    saving.value = false
    editingConnection.value = connection
    Object.assign(connectionForm, {
      name: connection?.name ?? '',
      base_url: connection?.base_url ?? '',
      api_key: ''
    })
    connectionOpen.value = true
    modalError.value = ''
  }
  function closeConnection(): void {
    connectionOpen.value = false
    connectionForm.api_key = ''
    mutationScope.replace()
    saving.value = false
    modalError.value = ''
  }
  async function saveConnection(): Promise<void> {
    if (
      saving.value ||
      !(editingConnection.value ? canUpdateCredentials.value : canCreateCredentials.value)
    )
      return
    const editing = editingConnection.value
    if (!connectionForm.name.trim() || !connectionForm.base_url.trim()) {
      modalError.value = '请填写连接名称和 API 地址'
      return
    }
    if (requiresKey.value && !connectionForm.api_key.trim()) {
      modalError.value = editing ? '更改 API 地址时，请输入新 API Key' : '请输入 API Key'
      return
    }
    const context = mutationScope.replace()
    saving.value = true
    modalError.value = ''
    try {
      const body = { name: connectionForm.name.trim(), base_url: connectionForm.base_url.trim() }
      const connection = editing
        ? await updateConnection(
            editing.id,
            { ...body, ...(connectionForm.api_key ? { api_key: connectionForm.api_key } : {}) },
            context.signal
          )
        : await createConnection({ ...body, api_key: connectionForm.api_key }, context.signal)
      if (context.isCurrent()) {
        options.saved(connection, editing !== null)
        connectionOpen.value = false
        connectionForm.name = ''
        connectionForm.base_url = ''
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) modalError.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) {
        connectionForm.api_key = ''
        saving.value = false
      }
    }
  }
  watch(options.access.authority, (current, previous) => {
    mutationScope.replace()
    saving.value = false
    modalError.value = ''
    connectionForm.api_key = ''
    if (current?.id !== previous?.id) {
      connectionOpen.value = false
      editingConnection.value = null
      Object.assign(connectionForm, { name: '', base_url: '', api_key: '' })
    } else if (
      !(editingConnection.value ? canUpdateCredentials.value : canCreateCredentials.value)
    ) {
      connectionOpen.value = false
      editingConnection.value = null
    }
  })
  onBeforeUnmount(() => {
    mutationScope.dispose()
    connectionForm.api_key = ''
  })
  return {
    connectionOpen,
    editingConnection,
    connectionForm,
    requiresKey,
    saving,
    modalError,
    openConnection,
    closeConnection,
    saveConnection
  }
}
