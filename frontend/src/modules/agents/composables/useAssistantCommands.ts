import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted } from '../../../shared/http.ts'
import type { ContextTicket, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { identity } from '../../identity/public.ts'
import { isPreparation } from '../../knowledge/public.ts'
import { createAssistantConversation, sendAssistantMessage } from '../api/conversations.ts'
import type {
  AssistantConversation,
  AssistantTurn,
  getAssistantConversation
} from '../api/conversations.ts'
import type { useAssistantRunObservation } from './useAssistantRunObservation.ts'

export interface AssistantOutgoingTurn {
  requestId: string
  conversationId: string | null
  content: string
  stage: 'submitting' | 'preparing' | 'replying' | 'uncertain' | 'failed' | 'cancelled'
  runId: string | null
  error: string
}
interface PendingAssistantSend {
  owner: string
  agent: string
  conversationId: string | null
  createId: string
  requestId: string
  message: string
}
interface AssistantCommandsOptions {
  agentId: string
  selected: Ref<AssistantConversation | null>
  turns: Ref<AssistantTurn[]>
  outgoing: Ref<AssistantOutgoingTurn | null>
  loading: Ref<boolean>
  error: Ref<string>
  conversationScope: RequestScope
  runs: ReturnType<typeof useAssistantRunObservation>
  canSend: () => boolean
  conversationId: () => string
  openPendingConversation: (id: string) => Promise<void>
  conversationCreated: (id: string) => Promise<void>
  readDetail: (id: string, context: ContextTicket) => Promise<AssistantConversation | undefined>
  refreshList: () => Promise<void>
  scroll: () => Promise<void>
}

export function useAssistantCommands(options: AssistantCommandsOptions) {
  const {
    agentId,
    selected,
    turns,
    outgoing,
    loading,
    error,
    conversationScope,
    runs,
    readDetail,
    refreshList,
    scroll
  } = options
  const draft = ref('')
  const sending = ref(false)
  const pending = ref<PendingAssistantSend | null>(null)
  const recoveryBlocked = ref(false)
  const pendingKey = () => `zhiheng:assistant:${identity.value?.id ?? ''}:${agentId}:pending`
  const outgoingVisible = computed(() =>
    Boolean(
      outgoing.value &&
        !turns.value.some(
          (turn) =>
            turn.requestId === outgoing.value?.requestId || turn.run.id === outgoing.value?.runId
        )
    )
  )
  const outgoingRun = computed(() =>
    runs.activeRun.value?.id === outgoing.value?.runId ? runs.activeRun.value : null
  )

  function showOutgoing(
    requestId: string,
    content: string,
    conversationId: string | null,
    stage: AssistantOutgoingTurn['stage']
  ): void {
    outgoing.value = { requestId, content, conversationId, stage, runId: null, error: '' }
  }
  function savePending(value: PendingAssistantSend): void {
    sessionStorage.setItem(pendingKey(), JSON.stringify(value))
    pending.value = value
  }
  function clearPending(): void {
    sessionStorage.removeItem(pendingKey())
    pending.value = null
  }
  function restorePending(): void {
    try {
      const raw = sessionStorage.getItem(pendingKey())
      if (!raw) return
      const data: unknown = JSON.parse(raw)
      if (typeof data !== 'object' || data === null) throw new Error('invalid')
      const value = data as Record<string, unknown>
      if (
        typeof value.owner !== 'string' ||
        value.owner !== identity.value?.id ||
        value.agent !== agentId ||
        typeof value.createId !== 'string' ||
        typeof value.requestId !== 'string' ||
        typeof value.message !== 'string' ||
        !(value.conversationId === null || typeof value.conversationId === 'string')
      )
        throw new Error('invalid')
      pending.value = {
        owner: value.owner,
        agent: agentId,
        createId: value.createId,
        requestId: value.requestId,
        message: value.message,
        conversationId: value.conversationId
      }
      if (!value.conversationId && !options.conversationId())
        showOutgoing(value.requestId, value.message, null, 'uncertain')
    } catch {
      recoveryBlocked.value = true
      error.value = '无法读取原提交，请先核对历史对话及浏览器会话存储，再刷新重试。'
    }
  }

  function reconcileDetail(
    id: string,
    result: Awaited<ReturnType<typeof getAssistantConversation>>
  ): void {
    if (
      outgoing.value &&
      result.turns.some(
        (turn) =>
          turn.requestId === outgoing.value?.requestId || turn.run.id === outgoing.value?.runId
      )
    )
      outgoing.value = null
    if (
      result.preparingMessage &&
      !result.turns.some((turn) => turn.requestId === result.preparingMessage?.requestId)
    ) {
      showOutgoing(
        result.preparingMessage.requestId,
        result.preparingMessage.content,
        id,
        'preparing'
      )
    } else if (
      !outgoing.value &&
      pending.value?.conversationId === id &&
      !result.turns.some((turn) => turn.requestId === pending.value?.requestId)
    ) {
      showOutgoing(pending.value.requestId, pending.value.message, id, 'uncertain')
    }
  }

  async function send(retry = false): Promise<void> {
    if (sending.value || loading.value || runs.running.value || !identity.value) return
    if (!retry && (!options.canSend() || pending.value || !draft.value.trim())) return
    const intent = retry
      ? pending.value
      : {
          owner: identity.value.id,
          agent: agentId,
          conversationId: selected.value?.id ?? null,
          createId: createRequestId(),
          requestId: createRequestId(),
          message: draft.value.trim()
        }
    if (!intent) return
    // Persist the original identity before any mutation, so uncertain responses can be retried.
    try {
      savePending(intent)
    } catch {
      error.value = '无法保存提交恢复信息，请检查浏览器存储后重试'
      return
    }
    if (intent.conversationId && intent.conversationId !== selected.value?.id) {
      await options.openPendingConversation(intent.conversationId)
      if (runs.running.value || selected.value?.id !== intent.conversationId) return
    }
    if (intent.owner !== identity.value?.id) return
    const context = conversationScope.current()
    runs.reset()
    sending.value = true
    error.value = ''
    showOutgoing(intent.requestId, intent.message, intent.conversationId, 'submitting')
    draft.value = ''
    void scroll()
    try {
      if (!intent.conversationId) {
        const value = await createAssistantConversation(agentId, intent.createId, context.signal)
        if (!context.isCurrent()) return
        selected.value = value
        intent.conversationId = value.id
        if (outgoing.value) outgoing.value.conversationId = value.id
        savePending(intent)
        await options.conversationCreated(value.id)
        if (!context.isCurrent()) return
      }
      const result = await sendAssistantMessage(
        agentId,
        intent.conversationId,
        intent.message,
        intent.requestId,
        context.signal
      )
      if (!context.isCurrent()) return
      clearPending()
      sending.value = false
      void refreshList()
      if (isPreparation(result)) {
        if (outgoing.value) outgoing.value.stage = 'preparing'
        await runs.followKnowledge(result, context)
      } else {
        runs.project(result)
        if (outgoing.value) {
          outgoing.value.stage = 'replying'
          outgoing.value.runId = result.id
        }
        await readDetail(intent.conversationId, context)
        if (context.isCurrent()) await runs.follow(result, context)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        const definitive =
          reason instanceof ApiError && reason.status < 500 && reason.status !== 408
        if (definitive) clearPending()
        if (outgoing.value) {
          outgoing.value.error = errorMessage(reason)
          outgoing.value.stage = pending.value ? 'uncertain' : 'failed'
        } else error.value = errorMessage(reason)
        runs.disconnected.value = runs.running.value
      }
    } finally {
      if (context.isCurrent()) sending.value = false
    }
  }

  function resetConversation(): void {
    outgoing.value = null
    draft.value = ''
    sending.value = false
  }

  function resetIdentity(): void {
    resetConversation()
    pending.value = null
    recoveryBlocked.value = false
  }

  return {
    draft,
    sending,
    pending,
    recoveryBlocked,
    outgoingVisible,
    outgoingRun,
    restorePending,
    reconcileDetail,
    send,
    resetConversation,
    resetIdentity
  }
}
