import { createRequestId } from '../../../shared/requestId.ts'
import { onBeforeUnmount, ref } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { object, string } from '../../../shared/validation.ts'
import { identity } from '../../identity/public.ts'
import * as api from '../api/index.ts'
import { projectPreparation } from '../preparation.ts'
import type {
  KnowledgeBase,
  KnowledgeHit,
  KnowledgePreparation,
  RetrievalDiagnostics
} from '../api/index.ts'
import { useGraphQuery } from './useGraphQuery.ts'
import type { GraphQueryMode } from './useGraphQuery.ts'

interface QueryIntent {
  baseId: string
  query: string
  client_request_id: string
}

export function useKnowledgeQuery(
  baseId: Readonly<Ref<string>>,
  selected: Readonly<Ref<KnowledgeBase | null>>
) {
  const graphQuery = useGraphQuery(baseId)
  const graphTarget = ref(false)
  const selectionScope = new RequestScope()
  async function submitSelectedQuery(
    text: string,
    target: 'kb' | 'graph' = 'kb',
    mode: GraphQueryMode = 'mix'
  ): Promise<void> {
    if (target === 'graph') {
      if (queryBusy.value || queryRecoverable.value) return
      selectionScope.replace()
      graphTarget.value = true
      await graphQuery.submit(text, mode)
    } else {
      if (graphQuery.busy.value || graphQuery.recoverable.value) return
      selectionScope.replace()
      graphTarget.value = false
      graphQuery.clear()
      await submitQuery(text)
    }
  }
  async function restoreSelectedQuery(id: string): Promise<void> {
    if (id !== baseId.value) return
    const ticket = selectionScope.replace()
    graphTarget.value = graphQuery.hasSaved()
    const restored = await graphQuery.restore()
    if (!ticket.isCurrent() || id !== baseId.value) return
    graphTarget.value = restored
    if (!graphTarget.value) await restoreQuery(id)
  }
  const queryBusy = ref(false),
    queryRecoverable = ref(false),
    queryError = ref('')
  const queryResult = ref<{
      items: KnowledgeHit[]
      diagnostics?: RetrievalDiagnostics | null
    } | null>(null),
    preparation = ref<KnowledgePreparation | null>(null),
    elapsed = ref(0)
  const queryScope = new RequestScope()
  let queryIntent: QueryIntent | null = null
  let observationId: string | null = null
  const queryStorage = (id: string) => `zhiheng:knowledge-query:${identity.value?.id ?? ''}:${id}`
  const requestStorage = (id: string) => `${queryStorage(id)}:request`
  function storeIntent(id: string): void {
    try {
      sessionStorage.setItem(requestStorage(id), JSON.stringify(queryIntent))
    } catch {
      /* The current page still retains the request key. */
    }
  }
  function storePreparation(value: KnowledgePreparation, id: string): void {
    observationId = value.id
    try {
      sessionStorage.setItem(queryStorage(id), value.id)
      sessionStorage.removeItem(requestStorage(id))
    } catch {
      /* The current page still retains the preparation ID. */
    }
  }
  async function followPreparation(initial: KnowledgePreparation, id: string): Promise<void> {
    const ticket = queryScope.replace(),
      started = performance.now()
    queryBusy.value = !api.preparationFinished(initial)
    queryRecoverable.value = false
    queryError.value = ''
    try {
      const final = await api.observePreparation(
        initial,
        (value) => {
          if (!ticket.isCurrent() || id !== baseId.value) return
          preparation.value = projectPreparation(preparation.value, value)
          storePreparation(preparation.value, id)
          return preparation.value
        },
        ticket.signal
      )
      if (!ticket.isCurrent() || id !== baseId.value) return
      elapsed.value = Math.round(performance.now() - started)
      if (final.status === 'completed')
        queryResult.value = { items: final.hits, diagnostics: final.diagnostics ?? null }
      else
        queryError.value =
          final.error?.message ?? (final.status === 'cancelled' ? '检索已取消' : '检索未完成')
      queryBusy.value = false
      queryIntent = null
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        queryError.value = errorMessage(error)
        queryRecoverable.value = true
      }
    }
  }
  async function submitQuery(text: string): Promise<void> {
    if (queryBusy.value || queryRecoverable.value || !selected.value || !text.trim()) return
    const id = baseId.value,
      ticket = queryScope.replace()
    if (!queryIntent || queryIntent.baseId !== id || queryIntent.query !== text)
      queryIntent = { baseId: id, query: text, client_request_id: createRequestId() }
    queryBusy.value = true
    queryError.value = ''
    queryResult.value = null
    preparation.value = null
    observationId = null
    storeIntent(id)
    try {
      const initial = await api.queryBase(
        id,
        {
          query: queryIntent.query,
          top_k: 8,
          similarity_threshold: 0,
          client_request_id: queryIntent.client_request_id
        },
        ticket.signal
      )
      if (!ticket.isCurrent() || id !== baseId.value) return
      preparation.value = initial
      storePreparation(initial, id)
      await followPreparation(initial, id)
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        queryError.value = errorMessage(error)
        queryBusy.value = false
        queryRecoverable.value = !(error instanceof ApiError) || error.status >= 500
        if (!queryRecoverable.value) {
          queryIntent = null
          try {
            sessionStorage.removeItem(requestStorage(id))
          } catch {
            /* Optional persistence. */
          }
        }
      }
    }
  }
  async function recoverQuery(): Promise<void> {
    if (queryBusy.value && !queryRecoverable.value) return
    const id = baseId.value,
      ticket = queryScope.replace()
    queryRecoverable.value = false
    try {
      const knownId = preparation.value?.id ?? observationId
      if (knownId) {
        queryBusy.value = true
        const value = await api.getPreparation(knownId, ticket.signal)
        if (ticket.isCurrent() && id === baseId.value) await followPreparation(value, id)
      } else if (queryIntent) {
        queryRecoverable.value = false
        queryBusy.value = false
        await submitQuery(queryIntent.query)
      }
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        queryError.value = errorMessage(error)
        queryRecoverable.value = true
      }
    }
  }
  async function stopQuery(): Promise<void> {
    const knownId = preparation.value?.id ?? observationId
    if (!knownId) return
    const id = baseId.value,
      ticket = queryScope.replace()
    queryBusy.value = true
    queryError.value = ''
    try {
      const value = await api.cancelPreparation(knownId, ticket.signal)
      if (ticket.isCurrent() && id === baseId.value) await followPreparation(value, id)
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        queryError.value = errorMessage(error)
        queryRecoverable.value = true
      }
    }
  }
  function clearQuery(): void {
    if (queryBusy.value || queryRecoverable.value) return
    queryScope.replace()
    queryError.value = ''
    queryResult.value = null
    preparation.value = null
    elapsed.value = 0
    queryIntent = null
    observationId = null
    try {
      sessionStorage.removeItem(queryStorage(baseId.value))
      sessionStorage.removeItem(requestStorage(baseId.value))
    } catch {
      /* Optional browser persistence. */
    }
  }
  function resetQuery(): void {
    selectionScope.replace()
    graphQuery.reset()
    graphTarget.value = false
    queryScope.replace()
    queryBusy.value = false
    queryRecoverable.value = false
    queryError.value = ''
    queryResult.value = null
    preparation.value = null
    elapsed.value = 0
    queryIntent = null
    observationId = null
  }
  async function restoreQuery(id: string): Promise<void> {
    const ticket = queryScope.replace()
    let request: string | null = null,
      preparationId: string | null = null
    try {
      request = sessionStorage.getItem(requestStorage(id))
      preparationId = sessionStorage.getItem(queryStorage(id))
    } catch {
      /* Optional browser persistence. */
    }
    if (request) {
      try {
        const data = object(JSON.parse(request) as unknown)
        const query = string(data.query),
          key = string(data.client_request_id)
        if (!query.trim() || query.length > 8000 || !key) throw new Error('Invalid saved query')
        queryIntent = { baseId: id, query, client_request_id: key }
        queryRecoverable.value = true
        queryError.value = '上次检索接纳结果待确认，请恢复查看'
      } catch {
        queryError.value = '无法恢复上次检索，请重新输入问题'
        try {
          sessionStorage.removeItem(requestStorage(id))
        } catch {
          /* Optional persistence. */
        }
      }
      return
    }
    if (!preparationId) return
    observationId = preparationId
    queryBusy.value = true
    try {
      const value = await api.getPreparation(preparationId, ticket.signal)
      if (!ticket.isCurrent() || id !== baseId.value) return
      preparation.value = value
      await followPreparation(value, id)
    } catch (error) {
      if (ticket.isCurrent() && !isAborted(error)) {
        queryError.value = errorMessage(error)
        queryBusy.value = false
        queryRecoverable.value = !(error instanceof ApiError) || error.status >= 500
      }
    }
  }
  onBeforeUnmount(() => {
    selectionScope.dispose()
    queryScope.dispose()
  })
  return {
    graphQuery,
    graphTarget,
    submitSelectedQuery,
    restoreSelectedQuery,
    queryBusy,
    queryRecoverable,
    queryError,
    queryResult,
    preparation,
    elapsed,
    submitQuery,
    recoverQuery,
    stopQuery,
    clearQuery,
    resetQuery,
    restoreQuery
  }
}
