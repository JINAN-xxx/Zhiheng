import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import { ApiError, errorMessage, isAborted } from '../../../shared/http.ts'
import type { ContextTicket, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { identity } from '../../identity/public.ts'
import { isPreparation } from '../../knowledge/public.ts'
import {
  createAssistantConversation,
  getAssistantSubmissionReceipt,
  sendAssistantMessage
} from '../api/conversations.ts'
import type {
  AssistantConversation,
  AssistantTurn,
  getAssistantConversation
} from '../api/conversations.ts'
import type { AssistantAttachment } from '../api/conversations.ts'
import {
  assistantPendingKey,
  decodeAssistantSubmission,
  encodeAssistantSubmission
} from '../pendingStorage.ts'
import type { AssistantSubmission } from '../pendingStorage.ts'
import type { QueuedFile } from '../../../shared/attachmentQueue.ts'
import type { useAssistantRunObservation } from './useAssistantRunObservation.ts'

export interface AssistantOutgoingTurn {
  requestId: string
  conversationId: string | null
  content: string
  attachments: AssistantAttachment[]
  stage: 'submitting' | 'preparing' | 'replying' | 'uncertain' | 'failed' | 'cancelled'
  runId: string | null
  error: string
}
interface AssistantInput {
  message: string
  attachments: AssistantAttachment[]
}
interface AssistantCommandsOptions {
  agentId: string
  plaza: boolean
  selected: Ref<AssistantConversation | null>
  turns: Ref<AssistantTurn[]>
  outgoing: Ref<AssistantOutgoingTurn | null>
  loading: Ref<boolean>
  error: Ref<string>
  conversationScope: RequestScope
  runs: ReturnType<typeof useAssistantRunObservation>
  queuedFiles: Ref<QueuedFile[]>
  clearFiles: () => void
  canSend: () => boolean
  openPendingConversation: (id: string) => Promise<void>
  conversationCreated: (id: string) => Promise<void>
  readDetail: (id: string, context: ContextTicket) => Promise<AssistantConversation | undefined>
  refreshList: () => Promise<void>
  scroll: () => Promise<void>
}

export function useAssistantCommands(options: AssistantCommandsOptions) {
  const {
    agentId,
    plaza,
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
  const pending = ref<AssistantSubmission | null>(null)
  const pendingInput = ref<AssistantInput | null>(null)
  const retryInput = ref(false)
  const confirming = ref(false)
  const confirmationNeeded = computed(() => Boolean(pending.value && !retryInput.value))
  const recoveryBlocked = ref(false)
  const pendingKey = () => assistantPendingKey(identity.value?.id ?? '', agentId, plaza)
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
    stage: AssistantOutgoingTurn['stage'],
    attachments: AssistantAttachment[] = []
  ): void {
    outgoing.value = {
      requestId,
      content,
      attachments,
      conversationId,
      stage,
      runId: null,
      error: ''
    }
  }
  function savePending(value: AssistantSubmission): void {
    if (value.owner !== identity.value?.id) return
    sessionStorage.setItem(
      assistantPendingKey(value.owner, agentId, plaza),
      encodeAssistantSubmission(value)
    )
    pending.value = value
  }
  function clearPending(): void {
    sessionStorage.removeItem(pendingKey())
    pending.value = null
    pendingInput.value = null
    retryInput.value = false
  }
  function restorePending(): void {
    try {
      const raw = sessionStorage.getItem(pendingKey())
      if (!raw) return
      const value = decodeAssistantSubmission(raw)
      if (!value || value.owner !== identity.value?.id || value.agent !== agentId)
        throw new Error('invalid')
      pending.value = value
    } catch {
      recoveryBlocked.value = true
      error.value = '无法读取待确认记录，请刷新后查看历史对话。'
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
        'preparing',
        result.preparingMessage.attachments
      )
    } else if (
      !outgoing.value &&
      pendingInput.value &&
      pending.value?.conversationId === id &&
      !result.turns.some((turn) => turn.requestId === pending.value?.requestId)
    ) {
      showOutgoing(
        pending.value.requestId,
        pendingInput.value.message,
        id,
        'uncertain',
        pendingInput.value.attachments
      )
    }
  }

  async function confirmReceipt(intent: AssistantSubmission): Promise<void> {
    const context = conversationScope.current()
    confirming.value = true
    error.value = ''
    try {
      const receipt = await getAssistantSubmissionReceipt(
        agentId,
        intent.requestId,
        intent.createId,
        intent.conversationId,
        context.signal,
        plaza
      )
      if (!context.isCurrent()) return
      if (receipt.accepted) {
        if (!receipt.conversationId) throw new Error('无法读取原提交所属的对话')
        clearPending()
        await options.openPendingConversation(receipt.conversationId)
        return
      }
      // The original submission may still be preprocessing. Re-entered input must use its request ID.
      const conversationId = receipt.conversationId ?? intent.conversationId
      savePending({ ...intent, conversationId })
      if (conversationId !== (selected.value?.id ?? null))
        await options.openPendingConversation(conversationId ?? '')
      if (identity.value?.id === intent.owner && pending.value?.requestId === intent.requestId)
        retryInput.value = true
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (identity.value?.id === intent.owner) confirming.value = false
    }
  }

  async function send(retry = false): Promise<void> {
    if (sending.value || confirming.value || loading.value || runs.running.value || !identity.value)
      return
    if (
      !retry &&
      (!options.canSend() ||
        confirmationNeeded.value ||
        (!draft.value.trim() && !options.queuedFiles.value.length) ||
        options.queuedFiles.value.some((file) => file.status !== 'ready' || !file.fileId))
    )
      return
    const saved = pending.value
    if (retry && saved && !pendingInput.value) return confirmReceipt(saved)
    const intent = saved ?? {
      owner: identity.value.id,
      agent: agentId,
      conversationId: selected.value?.id ?? null,
      createId: createRequestId(),
      requestId: createRequestId()
    }
    const input = retry
      ? pendingInput.value
      : {
          message: draft.value.trim() || '请分析附件。',
          attachments: options.queuedFiles.value.map((file) => ({
            file_id: file.fileId!,
            original_filename: file.fileName,
            mime: file.kind === 'image' ? 'image/*' : 'application/octet-stream',
            size: file.sizeBytes
          }))
        }
    if (!input) return
    // Persist the original identity before any mutation, so uncertain responses can be retried.
    try {
      savePending(intent)
      pendingInput.value = input
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
    retryInput.value = false
    runs.reset()
    sending.value = true
    error.value = ''
    showOutgoing(
      intent.requestId,
      input.message,
      intent.conversationId,
      'submitting',
      input.attachments
    )
    options.clearFiles()
    draft.value = ''
    void scroll()
    try {
      if (!intent.conversationId) {
        const value = await createAssistantConversation(
          agentId,
          intent.createId,
          context.signal,
          plaza
        )
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
        input.message,
        intent.requestId,
        context.signal,
        input.attachments.map((file) => file.file_id)
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
        if (definitive && !saved) clearPending()
        if (saved && reason instanceof ApiError && reason.code === 'idempotency_conflict') {
          pendingInput.value = null
          outgoing.value = null
        }
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
    options.clearFiles()
    sending.value = false
    retryInput.value = false
  }

  function resetIdentity(): void {
    resetConversation()
    pending.value = null
    pendingInput.value = null
    confirming.value = false
    recoveryBlocked.value = false
  }

  return {
    draft,
    sending,
    pending,
    confirmationNeeded,
    retryInput,
    confirming,
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
