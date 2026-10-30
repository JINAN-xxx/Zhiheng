import { onBeforeUnmount, ref } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { waitForBackgroundObservation } from '../../../shared/backgroundObservation.ts'
import { object, string } from '../../../shared/validation.ts'
import { identity } from '../../identity/public.ts'
import * as api from '../api/graph.ts'
import type { GraphJob, GraphQueryResult } from '../api/graph.ts'
import { graphIsActive } from '../graphPresentation.ts'

export type GraphQueryMode = 'mix' | 'local' | 'global' | 'hybrid' | 'naive'
interface Intent {
  query: string
  mode: GraphQueryMode
  top_k: number
  client_request_id: string
}
export function useGraphQuery(baseId: Readonly<Ref<string>>) {
  const busy = ref(false),
    recoverable = ref(false),
    error = ref(''),
    result = ref<GraphQueryResult | null>(null),
    elapsed = ref(0)
  const scope = new RequestScope()
  const selectedMode = ref<GraphQueryMode>('mix')
  let intent: Intent | null = null,
    current: GraphJob | null = null,
    cancelRequested = false
  let knownJobId: string | null = null
  const key = () => `zhiheng:graph-query:${identity.value?.id ?? ''}:${baseId.value}`
  function persist(): boolean {
    try {
      sessionStorage.setItem(
        key(),
        JSON.stringify({
          intent,
          jobId: current?.id ?? knownJobId,
          cancelRequested,
          settled: !!current && !graphIsActive(current)
        })
      )
      return true
    } catch {
      return false
    }
  }
  async function follow(initial: GraphJob, id: string): Promise<void> {
    const ticket = scope.replace(),
      started = performance.now()
    current = initial
    knownJobId = initial.id
    busy.value = true
    recoverable.value = false
    error.value = ''
    persist()
    try {
      while (graphIsActive(current)) {
        if (cancelRequested && current.cancel_allowed) {
          current = await api.cancelGraphJob(id, current.id, ticket.signal)
          if (!ticket.isCurrent() || id !== baseId.value) return
          cancelRequested = false
          persist()
          continue
        }
        await waitForBackgroundObservation(ticket.signal)
        const job = await api.getGraphJob(id, current.id, ticket.signal)
        if (!ticket.isCurrent() || id !== baseId.value) return
        if (job.version >= current.version) current = job
      }
      if (!ticket.isCurrent()) return
      elapsed.value = Math.round(performance.now() - started)
      if (current.status === 'completed' && current.result && 'context' in current.result)
        result.value = current.result
      else
        error.value =
          current.error?.message ??
          (current.status === 'cancelled' ? '图谱检索已取消' : '图谱检索未完成')
      busy.value = false
      intent = null
      cancelRequested = false
      persist()
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        busy.value = false
        recoverable.value = true
      }
    }
  }
  async function send(): Promise<void> {
    if (!intent) return
    const id = baseId.value,
      ticket = scope.replace()
    busy.value = true
    recoverable.value = false
    error.value = ''
    result.value = null
    try {
      const job = await api.queryGraph(id, intent, ticket.signal)
      if (ticket.isCurrent() && id === baseId.value) await follow(job, id)
    } catch (reason) {
      if (ticket.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        busy.value = false
        recoverable.value = !(
          reason instanceof ApiError &&
          reason.status >= 400 &&
          reason.status < 500
        )
        if (!recoverable.value) {
          intent = null
          persist()
        }
      }
    }
  }
  async function submit(query: string, mode: GraphQueryMode): Promise<void> {
    if (busy.value || recoverable.value || !query.trim()) return
    current = null
    knownJobId = null
    selectedMode.value = mode
    cancelRequested = false
    result.value = null
    intent = { query, mode, top_k: 8, client_request_id: crypto.randomUUID() }
    if (!persist()) {
      error.value = '无法保存请求标识，请恢复浏览器存储后重试'
      intent = null
      return
    }
    await send()
  }
  async function recover(): Promise<void> {
    const id = baseId.value,
      ticket = scope.replace()
    if (current || knownJobId) {
      try {
        const job = await api.getGraphJob(id, current?.id ?? knownJobId!, ticket.signal)
        if (ticket.isCurrent() && id === baseId.value) await follow(job, id)
      } catch (reason) {
        if (ticket.isCurrent() && !isAborted(reason)) {
          result.value = null
          error.value = errorMessage(reason)
          recoverable.value = true
        }
      }
    } else await send()
  }
  async function stop(): Promise<void> {
    if (!current || cancelRequested) return
    cancelRequested = true
    persist()
    await recover()
  }
  function reset(): void {
    scope.replace()
    intent = null
    current = null
    knownJobId = null
    selectedMode.value = 'mix'
    cancelRequested = false
    busy.value = false
    recoverable.value = false
    error.value = ''
    result.value = null
    elapsed.value = 0
  }
  function clear(): void {
    if (busy.value || recoverable.value) return
    reset()
    try {
      sessionStorage.removeItem(key())
    } catch {
      /* Optional storage cleanup. */
    }
  }
  function hasSaved(): boolean {
    try {
      return sessionStorage.getItem(key()) !== null
    } catch {
      return false
    }
  }
  function forgetSettledRestore(): void {
    intent = null
    current = null
    knownJobId = null
    cancelRequested = false
    try {
      sessionStorage.removeItem(key())
    } catch {
      persist()
    }
  }
  function denyProtectedResults(message: string): void {
    const settled = !!current && !graphIsActive(current)
    scope.replace()
    result.value = null
    busy.value = false
    recoverable.value = !settled
    error.value = message
    if (settled) forgetSettledRestore()
    else {
      if (current) current = { ...current, result: null }
      persist()
    }
  }
  async function restore(): Promise<boolean> {
    let raw: string | null
    try {
      raw = sessionStorage.getItem(key())
    } catch {
      return false
    }
    if (!raw) return false
    const id = baseId.value,
      ticket = scope.replace()
    let restoredSettled = false
    try {
      const saved = object(JSON.parse(raw) as unknown)
      restoredSettled = saved.settled === true
      if (saved.intent !== null) {
        const body = object(saved.intent),
          mode = body.mode
        if (!['mix', 'local', 'global', 'hybrid', 'naive'].includes(string(mode)))
          throw new Error('Invalid mode')
        intent = {
          query: string(body.query),
          mode: mode as GraphQueryMode,
          top_k: 8,
          client_request_id: string(body.client_request_id)
        }
        selectedMode.value = intent.mode
      }
      cancelRequested = saved.cancelRequested === true
      if (saved.jobId !== null) {
        knownJobId = string(saved.jobId)
        busy.value = true
        const job = await api.getGraphJob(id, knownJobId, ticket.signal)
        if (!ticket.isCurrent() || id !== baseId.value) return false
        if (cancelRequested && graphIsActive(job)) {
          current = job
          busy.value = false
          recoverable.value = true
          error.value = '上次图谱检索停止结果待确认，请恢复查看'
          persist()
        } else await follow(job, id)
      } else if (intent) {
        recoverable.value = true
        error.value = '上次图谱检索接纳结果待确认，请恢复查看'
      }
      return !!current || !!intent || !!knownJobId
    } catch (reason) {
      if (!ticket.isCurrent() || id !== baseId.value || isAborted(reason)) return false
      result.value = null
      busy.value = false
      error.value = errorMessage(reason)
      if (
        restoredSettled &&
        reason instanceof ApiError &&
        [401, 403, 404].includes(reason.status)
      ) {
        forgetSettledRestore()
        recoverable.value = false
      } else recoverable.value = true
      return true
    }
  }
  onBeforeUnmount(() => scope.dispose())
  return {
    busy,
    recoverable,
    error,
    result,
    elapsed,
    selectedMode,
    hasSaved,
    denyProtectedResults,
    submit,
    recover,
    stop,
    reset,
    clear,
    restore
  }
}
