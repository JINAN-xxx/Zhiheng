import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  bindResource,
  configureResourceQuota,
  getApplication,
  getResourceAccess,
  getResourceCandidates,
  unbindResource
} from '../api/index.ts'
import type {
  Application,
  BoundResource,
  ResourceAccess,
  ResourceAction,
  ResourceCandidate,
  ResourceKind
} from '../api/index.ts'

export function useResourceAccess(
  application: () => Application,
  update: (
    version: number,
    limits: { sourceFileLimit: number; sourceByteLimit: number },
    refreshed?: Application
  ) => void
) {
  const read = new RequestScope(),
    discovery = new RequestScope(),
    mutation = new RequestScope()
  const snapshot = ref<ResourceAccess | null>(null),
    candidates = ref<ResourceCandidate[]>([])
  const loading = ref(false),
    candidateLoading = ref(false),
    saving = ref(false)
  const error = ref(''),
    formError = ref(''),
    candidateError = ref('')
  const bindingOpen = ref(false),
    quotaOpen = ref(false),
    editing = ref(false)
  const form = reactive({
    kind: 'file' as ResourceKind,
    resourceId: '',
    actions: [] as ResourceAction[]
  })
  const quota = reactive({ fileLimit: 0, byteLimit: 0 })
  const selected = computed(() =>
    candidates.value.find((item) => item.resourceId === form.resourceId)
  )
  async function load(): Promise<void> {
    const id = application().applicationId,
      ticket = read.replace()
    loading.value = true
    error.value = ''
    try {
      const result = await getResourceAccess(id, ticket.signal)
      if (!ticket.isCurrent() || application().applicationId !== id) return
      snapshot.value = result
      update(result.applicationVersion, {
        sourceFileLimit: result.sourceFileLimit,
        sourceByteLimit: result.sourceByteLimit
      })
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  }
  async function loadCandidates(): Promise<void> {
    const id = application().applicationId,
      kind = form.kind,
      ticket = discovery.replace()
    candidateLoading.value = true
    candidateError.value = ''
    try {
      const result = await getResourceCandidates(id, kind, ticket.signal)
      if (ticket.isCurrent()) candidates.value = result
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) candidateError.value = errorMessage(reason)
    } finally {
      if (ticket.isCurrent()) candidateLoading.value = false
    }
  }
  function closeBinding(): void {
    mutation.replace()
    discovery.replace()
    bindingOpen.value = false
    saving.value = false
    candidates.value = []
    candidateError.value = ''
    formError.value = ''
  }
  async function openBinding(row?: BoundResource): Promise<void> {
    form.kind = row?.kind ?? 'file'
    form.resourceId = row?.resourceId ?? ''
    form.actions = row ? row.actions.filter((action) => row.allowedActions.includes(action)) : []
    editing.value = !!row
    bindingOpen.value = true
    formError.value = ''
    candidates.value = row ? [row] : []
    await loadCandidates()
  }
  async function changeKind(value: unknown): Promise<void> {
    if (value !== 'file' && value !== 'knowledge_base' && value !== 'skill') return
    form.kind = value
    form.resourceId = ''
    form.actions = []
    candidates.value = []
    await loadCandidates()
  }
  function changeCandidate(value: unknown): void {
    form.resourceId = typeof value === 'string' ? value : ''
    form.actions = []
  }
  async function mutate(
    action: (signal: AbortSignal) => Promise<number>,
    close: () => void
  ): Promise<void> {
    if (saving.value || !snapshot.value) return
    const ticket = mutation.replace()
    saving.value = true
    formError.value = ''
    try {
      const version = await action(ticket.signal)
      if (!ticket.isCurrent()) return
      update(version, {
        sourceFileLimit: snapshot.value.sourceFileLimit,
        sourceByteLimit: snapshot.value.sourceByteLimit
      })
      close()
      await load()
    } catch (reason) {
      if (!ticket.isCurrent() || isAborted(reason)) return
      if (reason instanceof ApiError && reason.code === 'application_conflict') {
        try {
          const refreshed = await getApplication(application().applicationId, ticket.signal)
          if (!ticket.isCurrent()) return
          update(
            refreshed.version,
            {
              sourceFileLimit: refreshed.sourceFileLimit,
              sourceByteLimit: refreshed.sourceByteLimit
            },
            refreshed
          )
        } catch (refreshError) {
          if (ticket.isCurrent() && !isAborted(refreshError))
            formError.value = `版本刷新失败：${errorMessage(refreshError)}。你的输入已保留。`
          return
        }
        await load()
        if (!ticket.isCurrent()) return
        if (bindingOpen.value) await loadCandidates()
        if (ticket.isCurrent())
          formError.value = '应用已变化，已刷新当前版本。你的输入已保留，请检查后重新提交。'
      } else formError.value = errorMessage(reason)
    } finally {
      if (ticket.isCurrent()) saving.value = false
    }
  }
  async function saveBinding(): Promise<void> {
    if (
      !selected.value ||
      !form.actions.length ||
      form.actions.some((action) => !selected.value?.allowedActions.includes(action))
    ) {
      formError.value = '请选择资源及当前允许的动作'
      return
    }
    await mutate(
      (signal) =>
        bindResource(
          application().applicationId,
          form.kind,
          form.resourceId,
          [...form.actions],
          application().version,
          signal
        ),
      () => {
        bindingOpen.value = false
      }
    )
  }
  async function remove(row: BoundResource): Promise<void> {
    await mutate(
      (signal) =>
        unbindResource(
          application().applicationId,
          row.kind,
          row.resourceId,
          application().version,
          signal
        ),
      () => {}
    )
  }
  function openQuota(): void {
    if (!snapshot.value) return
    quota.fileLimit = snapshot.value.sourceFileLimit
    quota.byteLimit = snapshot.value.sourceByteLimit
    quotaOpen.value = true
    formError.value = ''
  }
  function closeQuota(): void {
    mutation.replace()
    quotaOpen.value = false
    saving.value = false
    formError.value = ''
  }
  async function saveQuota(): Promise<void> {
    if (
      ![quota.fileLimit, quota.byteLimit].every((value) => Number.isSafeInteger(value) && value > 0)
    ) {
      formError.value = '额度必须为正整数'
      return
    }
    await mutate(
      (signal) =>
        configureResourceQuota(
          application().applicationId,
          quota.fileLimit,
          quota.byteLimit,
          application().version,
          signal
        ),
      () => {
        quotaOpen.value = false
      }
    )
  }
  watch(
    () => application().applicationId,
    () => {
      closeBinding()
      closeQuota()
      snapshot.value = null
      void load()
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    read.dispose()
    discovery.dispose()
    mutation.dispose()
  })
  return {
    snapshot,
    candidates,
    loading,
    candidateLoading,
    saving,
    error,
    formError,
    candidateError,
    bindingOpen,
    quotaOpen,
    editing,
    form,
    quota,
    selected,
    load,
    loadCandidates,
    openBinding,
    closeBinding,
    changeKind,
    changeCandidate,
    saveBinding,
    remove,
    openQuota,
    closeQuota,
    saveQuota
  }
}
