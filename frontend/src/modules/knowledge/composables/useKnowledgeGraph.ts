import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { object, string } from '../../../shared/validation.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity } from '../../identity/public.ts'
import * as api from '../api/graph.ts'
import type { GraphConfigInput, GraphConfigState, GraphData, GraphJob } from '../api/graph.ts'
import { waitForObservation } from '../api/index.ts'
import { graphIsActive } from '../graphPresentation.ts'

interface PendingBuild {
  baseId: string
  operation: 'build' | 'preview'
  body: {
    client_request_id: string
    expected_version: number
    rebuild?: boolean
    document_id?: string | null
  }
}
export function useKnowledgeGraph(baseId: Ref<string>, enabled: Ref<boolean>) {
  const state = ref<GraphConfigState | null>(null),
    graph = ref<GraphData | null>(null),
    labels = ref<string[]>([])
  const jobs = ref<GraphJob[]>([]),
    loading = ref(false),
    saving = ref(false),
    starting = ref(false),
    stopping = ref(false)
  const error = ref(''),
    uncertain = ref(false),
    configUncertain = ref(false),
    recoverable = ref(false)
  const pending = ref<PendingBuild | null>(null),
    cancelId = ref<string | null>(null)
  const readScope = new RequestScope(),
    graphScope = new RequestScope(),
    writeScope = new RequestScope(),
    pollScope = new RequestScope()
  const build = computed(() => jobs.value.find((job) => job.operation === 'build') ?? null)
  const preview = computed(() => jobs.value.find((job) => job.operation === 'preview') ?? null)
  const active = computed(() => jobs.value.some(graphIsActive))
  const busy = computed(
    () => saving.value || starting.value || active.value || uncertain.value || configUncertain.value
  )
  const storageKey = () => `zhiheng:graph-intent:${identity.value?.id ?? ''}:${baseId.value}`
  function persist(): boolean {
    try {
      if (pending.value || cancelId.value)
        sessionStorage.setItem(
          storageKey(),
          JSON.stringify({ pending: pending.value, cancelId: cancelId.value })
        )
      else sessionStorage.removeItem(storageKey())
      return true
    } catch {
      return false
    }
  }
  let protectedEpoch = 0
  function withoutProtectedResults(job: GraphJob): GraphJob {
    return {
      ...job,
      result: null,
      processed_documents: null,
      total_documents: null,
      progress: { extracted_chunks: null, total_chunks: null, entities: null, relations: null }
    }
  }
  function merge(incoming: GraphJob, epoch = protectedEpoch): void {
    const job = epoch === protectedEpoch ? incoming : withoutProtectedResults(incoming)
    const current = jobs.value.find((item) => item.id === job.id)
    if (current && current.version > job.version) return
    if (current && !graphIsActive(current) && graphIsActive(job)) return
    jobs.value = [job, ...jobs.value.filter((item) => item.id !== job.id)].sort((left, right) =>
      right.created_at.localeCompare(left.created_at)
    )
  }
  function hideProtectedResults(): void {
    protectedEpoch += 1
    readScope.replace()
    graphScope.replace()
    pollScope.replace()
    loading.value = false
    graph.value = null
    labels.value = []
    jobs.value = jobs.value.map(withoutProtectedResults)
    recoverable.value = active.value || !!pending.value || !!cancelId.value
  }
  async function loadGraph(
    options = { node_label: '*', max_depth: 2, max_nodes: 100 }
  ): Promise<void> {
    const id = baseId.value,
      ticket = graphScope.replace()
    if (!state.value?.active_revision || state.value.requires_rebuild) {
      graph.value = null
      labels.value = []
      return
    }
    try {
      const [data, names] = await Promise.all([
        api.getGraphData(id, options, ticket.signal),
        api.getGraphLabels(id, ticket.signal)
      ])
      if (!ticket.isCurrent() || id !== baseId.value) return
      graph.value = data
      labels.value = names.items
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        hideProtectedResults()
        error.value = errorMessage(reason)
      }
    }
  }
  async function load(): Promise<void> {
    const id = baseId.value,
      ticket = readScope.replace()
    if (!id || !enabled.value) return
    loading.value = true
    error.value = ''
    try {
      const config = await api.getGraphConfig(id, ticket.signal)
      const rows = config.can_manage ? await api.listGraphJobs(id, ticket.signal) : []
      if (!ticket.isCurrent() || id !== baseId.value) return
      state.value = config
      for (const job of rows) merge(job)
      configUncertain.value = false
      await loadGraph()
      if (ticket.isCurrent()) void observe()
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        if (reason instanceof ApiError && [401, 403, 404].includes(reason.status))
          state.value = null
        hideProtectedResults()
      }
    } finally {
      if (ticket.isCurrent()) loading.value = false
    }
  }
  async function observe(): Promise<void> {
    const id = baseId.value,
      ticket = pollScope.replace()
    recoverable.value = !!cancelId.value
    try {
      while (ticket.isCurrent() && active.value) {
        await waitForObservation(ticket.signal, 700)
        for (const job of jobs.value.filter(graphIsActive)) {
          const result = await api.getGraphJob(id, job.id, ticket.signal)
          if (!ticket.isCurrent() || id !== baseId.value) return
          merge(result)
        }
      }
      if (ticket.isCurrent() && state.value && !active.value) {
        const config = await api.getGraphConfig(id, ticket.signal)
        if (!ticket.isCurrent() || id !== baseId.value) return
        state.value = config
        await loadGraph()
      }
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        if (reason instanceof ApiError && [401, 403, 404].includes(reason.status))
          hideProtectedResults()
        recoverable.value = true
      }
    }
  }
  async function save(config: GraphConfigInput): Promise<boolean> {
    if (
      saving.value ||
      starting.value ||
      active.value ||
      uncertain.value ||
      configUncertain.value ||
      !state.value?.can_manage
    )
      return false
    const id = baseId.value,
      ticket = writeScope.replace()
    saving.value = true
    error.value = ''
    try {
      const result = await api.saveGraphConfig(
        id,
        { expected_version: state.value.config?.version ?? 0, ...config },
        ticket.signal
      )
      if (!ticket.isCurrent() || id !== baseId.value) return false
      state.value = result
      return true
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        configUncertain.value = !(
          reason instanceof ApiError &&
          reason.status >= 400 &&
          reason.status < 500
        )
      }
      return false
    } finally {
      if (ticket.isCurrent()) saving.value = false
    }
  }
  async function submitPending(): Promise<void> {
    const intent = pending.value
    if (!intent || starting.value || intent.baseId !== baseId.value || !state.value?.can_manage)
      return
    const ticket = writeScope.replace(),
      epoch = protectedEpoch
    starting.value = true
    error.value = ''
    try {
      const job = await api.startGraphBuild(
        intent.baseId,
        intent.operation,
        intent.body,
        ticket.signal
      )
      if (!ticket.isCurrent() || intent.baseId !== baseId.value) return
      merge(job, epoch)
      pending.value = null
      uncertain.value = false
      persist()
      void observe()
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        const rejected = reason instanceof ApiError && reason.status >= 400 && reason.status < 500
        if (rejected) {
          pending.value = null
          uncertain.value = false
          persist()
        } else {
          uncertain.value = true
          persist()
        }
      }
    } finally {
      if (ticket.isCurrent()) starting.value = false
    }
  }
  async function start(
    config: GraphConfigInput,
    operation: 'build' | 'preview',
    extra: { rebuild?: boolean; document_id?: string | null } = {}
  ): Promise<void> {
    if (busy.value || !state.value?.can_manage) return
    const current = state.value.config
    const changed =
      !current ||
      Object.entries(config).some(
        ([key, value]) => current[key as keyof GraphConfigInput] !== value
      )
    if (changed && !(await save(config))) return
    pending.value = {
      baseId: baseId.value,
      operation,
      body: {
        client_request_id: crypto.randomUUID(),
        expected_version: state.value.config?.version ?? 0,
        ...extra
      }
    }
    uncertain.value = true
    if (!persist()) {
      error.value = '无法保存本次请求标识，请恢复浏览器存储后重试'
      pending.value = null
      uncertain.value = false
      return
    }
    await submitPending()
  }
  async function cancel(job: GraphJob): Promise<void> {
    if (stopping.value || !job.cancel_allowed || !state.value?.can_manage) return
    const id = baseId.value,
      ticket = writeScope.replace(),
      epoch = protectedEpoch
    cancelId.value = job.id
    persist()
    stopping.value = true
    error.value = ''
    try {
      const result = await api.cancelGraphJob(id, job.id, ticket.signal)
      if (!ticket.isCurrent() || id !== baseId.value) return
      merge(result, epoch)
      cancelId.value = null
      persist()
      void observe()
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        recoverable.value = true
      }
    } finally {
      if (ticket.isCurrent()) stopping.value = false
    }
  }
  async function recover(): Promise<void> {
    if (pending.value) {
      await submitPending()
      return
    }
    if (cancelId.value) {
      const job = jobs.value.find((item) => item.id === cancelId.value)
      if (job) {
        if (!job.cancel_allowed) {
          cancelId.value = null
          persist()
          await load()
          return
        }
        await cancel(job)
        return
      }
    }
    await load()
  }
  watch(
    [baseId, enabled],
    () => {
      for (const scope of [readScope, graphScope, writeScope, pollScope]) scope.replace()
      state.value = null
      graph.value = null
      labels.value = []
      jobs.value = []
      error.value = ''
      pending.value = null
      cancelId.value = null
      uncertain.value = false
      configUncertain.value = false
      loading.value = false
      saving.value = false
      starting.value = false
      stopping.value = false
      recoverable.value = false
      if (baseId.value && enabled.value) {
        try {
          const raw = sessionStorage.getItem(storageKey())
          if (raw) {
            const saved = object(JSON.parse(raw) as unknown)
            if (saved.pending !== null) {
              const value = object(saved.pending),
                body = object(value.body)
              const operation = value.operation
              if (
                value.baseId !== baseId.value ||
                (operation !== 'build' && operation !== 'preview')
              )
                throw new Error('Invalid graph intent')
              const requestId = string(body.client_request_id)
              if (
                !Number.isInteger(body.expected_version) ||
                typeof body.expected_version !== 'number' ||
                body.expected_version < 0
              )
                throw new Error('Invalid graph version')
              if (operation === 'build' && typeof body.rebuild !== 'boolean')
                throw new Error('Invalid graph build')
              if (
                operation === 'preview' &&
                body.document_id !== null &&
                typeof body.document_id !== 'string'
              )
                throw new Error('Invalid graph preview')
              pending.value = {
                baseId: baseId.value,
                operation,
                body:
                  operation === 'build'
                    ? {
                        client_request_id: requestId,
                        expected_version: body.expected_version,
                        rebuild: body.rebuild as boolean
                      }
                    : {
                        client_request_id: requestId,
                        expected_version: body.expected_version,
                        document_id: body.document_id as string | null
                      }
              }
              uncertain.value = true
            }
            cancelId.value = saved.cancelId === null ? null : string(saved.cancelId)
            recoverable.value = !!cancelId.value
          }
        } catch {
          error.value = '图谱请求恢复信息不可用，请刷新核对已有任务后再操作'
          configUncertain.value = true
        }
        void load()
      }
    },
    { immediate: true }
  )
  onBeforeRouteLeave(
    () => !(starting.value || saving.value) && (!(pending.value || cancelId.value) || persist())
  )
  onBeforeUnmount(() => {
    for (const scope of [readScope, graphScope, writeScope, pollScope]) scope.dispose()
  })
  return {
    state,
    graph,
    labels,
    jobs,
    build,
    preview,
    loading,
    saving,
    starting,
    stopping,
    error,
    uncertain,
    configUncertain,
    recoverable,
    busy,
    load,
    loadGraph,
    save,
    start,
    cancel,
    recover
  }
}
