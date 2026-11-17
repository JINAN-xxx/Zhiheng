import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { errorMessage, isAborted, isDefiniteRejection } from '../../../shared/http.ts'
import type { ContextTicket, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { identity, canUseCapability } from '../../identity/public.ts'
import { createConversation, sendMessage } from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { nextSendIntent } from '../api/sendIntent.ts'
import type { SendInput, SendIntent } from '../api/sendIntent.ts'
import { generationRequest, generationSettingsError } from '../generationSettings.ts'
import { regenerateMessage } from '../api/messageActions.ts'
import { groupMessageVersions, nextRegenerationIntent } from './messageVersions.ts'
import type { RegenerationIntent } from './messageVersions.ts'
import { inputIssueFrom } from '../inputIssue.ts'
import type { ChatInputIssue } from '../inputIssue.ts'
import type { useChatComposer } from './composer.ts'
import type { useChatRunObservation } from './runObservation.ts'

interface CommandOptions {
  selected: Ref<Conversation | null>
  conversationScope: RequestScope
  error: Ref<string>
  readOnly: Ref<boolean>
  loading: Ref<boolean>
  listLoading: Ref<boolean>
  historyReady: Ref<boolean>
  messages: Ref<ChatMessage[]>
  composer: ReturnType<typeof useChatComposer>
  runs: ReturnType<typeof useChatRunObservation>
  onConversationCreated: (conversation: Conversation) => void
  acceptMainRun: (
    run: RunSnapshot,
    context: ContextTicket,
    clearComposer?: boolean
  ) => Promise<void>
}

export function useChatCommands(options: CommandOptions) {
  const workspaceOwnerId = identity.value?.id
  let disposed = false
  const ownsWorkspace = () =>
    !disposed && workspaceOwnerId !== undefined && identity.value?.id === workspaceOwnerId
  const {
    selected,
    conversationScope,
    error,
    readOnly,
    loading,
    listLoading,
    historyReady,
    messages,
    onConversationCreated,
    acceptMainRun
  } = options
  const {
    modelId,
    draft,
    queuedFiles,
    fileQueueReady,
    supportsVision,
    generationSettings,
    selectedModel,
    selectedSkills,
    skillPickerOpen
  } = options.composer
  const { running, disconnected } = options.runs
  const inputIssue = ref<ChatInputIssue | null>(null)

  const dismissInputIssue = () => {
    inputIssue.value = null
  }

  watch(
    [
      draft,
      modelId,
      generationSettings,
      () => queuedFiles.value.map((file) => file.fileId ?? file.id).join(','),
      () => selectedSkills.value.map((skill) => skill.skillSlug).join(',')
    ],
    dismissInputIssue
  )

  const sending = ref(false)

  const pendingRegenerations = reactive(new Map<string, RegenerationIntent>())

  const regenerationPending = computed(() =>
    Boolean(selected.value && pendingRegenerations.has(selected.value.id))
  )

  const canRetryRegeneration = computed(
    () => regenerationPending.value && !sending.value && !running.value
  )

  const pendingSends = reactive(new Map<string, SendIntent>())
  const pendingKey = () => selected.value?.id ?? ''

  const canRetry = ref(false)

  function composerInput(): SendInput {
    return {
      conversationId: selected.value?.id ?? null,
      model_id: modelId.value ?? '',
      ...generationRequest(generationSettings.value),
      message: draft.value.trim() || (queuedFiles.value.length ? '请分析上传的文件。' : ''),
      skill_slugs: selectedSkills.value.map((skill) => skill.skillSlug),
      file_ids: queuedFiles.value.map((file) => file.fileId!)
    }
  }
  const composerSignature = (input: SendInput) =>
    JSON.stringify([
      input.model_id,
      input.message,
      input.system_prompt ?? '',
      input.temperature ?? null,
      input.max_output_tokens ?? null,
      input.skill_slugs ?? [],
      input.file_ids ?? []
    ])

  async function send(retry = false): Promise<void> {
    if (!ownsWorkspace() || !canUseCapability('chat', 'use')) return
    const model = modelId.value
    const message = draft.value.trim() || (queuedFiles.value.length ? '请分析上传的文件。' : '')
    if (
      sending.value ||
      regenerationPending.value ||
      readOnly.value ||
      running.value ||
      loading.value ||
      listLoading.value ||
      !historyReady.value ||
      (!retry && (canRetry.value || !message || !model || !fileQueueReady.value))
    )
      return
    if (
      !retry &&
      queuedFiles.value.some((file) => file.kind === 'image') &&
      !supportsVision.value
    ) {
      error.value = '请选择已启用视觉能力的模型后发送图片'
      return
    }
    if (!retry) {
      const settingsError = generationSettingsError(
        generationSettings.value,
        selectedModel.value?.max_output_tokens
      )
      if (settingsError) {
        error.value = settingsError
        return
      }
    }
    const ownerId = identity.value?.id
    if (!ownerId) return
    const context = conversationScope.replace()
    const isCurrent = () => context.isCurrent() && identity.value?.id === ownerId
    let targetKey = pendingKey()
    const previous = pendingSends.get(targetKey) ?? null
    const request = retry
      ? previous
      : model
        ? nextSendIntent(previous, composerInput(), createRequestId)
        : null
    if (!request) return
    pendingSends.set(targetKey, request)
    sending.value = true
    skillPickerOpen.value = false
    error.value = ''
    dismissInputIssue()
    canRetry.value = false
    try {
      let conversationId = request.conversationId
      if (conversationId === null) {
        const conversation = await createConversation(context.signal)
        if (!isCurrent()) return
        onConversationCreated(conversation)
        conversationId = conversation.id
        pendingSends.delete(targetKey)
        targetKey = conversationId
        pendingSends.set(targetKey, { ...request, conversationId })
      }
      const admission = await sendMessage(
        conversationId,
        {
          model_id: request.model_id,
          ...generationRequest(request),
          message: request.message,
          client_request_id: request.client_request_id,
          skill_slugs: request.skill_slugs ?? [],
          file_ids: request.file_ids ?? []
        },
        context.signal
      )
      if (!isCurrent()) return
      const clearComposer = composerSignature(composerInput()) === composerSignature(request)
      pendingSends.delete(targetKey)
      await acceptMainRun(admission, context, clearComposer)
    } catch (reason) {
      if (isCurrent() && !isAborted(reason)) {
        const issue = inputIssueFrom(reason)
        if (issue && !retry && isDefiniteRejection(reason)) {
          inputIssue.value = issue
          pendingSends.delete(targetKey)
          canRetry.value = false
          error.value = ''
        } else {
          if (isDefiniteRejection(reason) && !retry) pendingSends.delete(targetKey)
          error.value = errorMessage(reason)
          canRetry.value = pendingSends.has(targetKey)
        }
        disconnected.value = running.value
      }
    } finally {
      if (isCurrent()) sending.value = false
    }
  }

  function regenerationReason(message: ChatMessage): string {
    if (!canUseCapability('chat', 'use')) return '当前账号没有重新生成权限'
    if (selected.value?.archived) return '会话已归档，取消归档后可重新生成'
    if (readOnly.value) return '会话状态待核对，请刷新会话'
    if (running.value || sending.value) return '当前正在生成或提交，请结束后重试'
    if (loading.value || !historyReady.value) return '请等待会话加载完成'
    if (canRetry.value) return '上一条发送结果待确认，请先使用原请求重试'
    const pending = selected.value ? pendingRegenerations.get(selected.value.id) : undefined
    if (pending && pending.messageId !== message.id)
      return '重新生成结果待确认，请使用原重新生成请求重试'
    const row = groupMessageVersions(messages.value).find((row) =>
      row.versions.some((item) => item.id === message.id)
    )
    const latestQuestion = [...messages.value].reverse().find((item) => item.role === 'user')
    if (!latestQuestion || row?.questionId !== latestQuestion.id) return '只能重新生成最新一轮回复'
    return messages.value.some((item) => item.id === message.id) ? '' : '请刷新会话后重试'
  }

  async function regenerate(message: ChatMessage): Promise<void> {
    if (!ownsWorkspace() || !canUseCapability('chat', 'use')) return
    const blocked = regenerationReason(message)
    if (blocked) {
      error.value = blocked
      return
    }
    const conversation = selected.value
    const owner = identity.value?.id
    if (!conversation || !owner) return
    const versions = groupMessageVersions(messages.value).find((row) =>
      row.versions.some((item) => item.id === message.id)
    )
    const highestGeneration = Math.max(
      ...(versions?.versions ?? [message]).map((item) => item.generation_index)
    )
    const previous = pendingRegenerations.get(conversation.id) ?? null
    const intent = nextRegenerationIntent(
      previous,
      conversation.id,
      {
        ...message,
        reply_to_message_id: versions?.questionId ?? message.reply_to_message_id,
        generation_index: highestGeneration
      },
      createRequestId
    )
    pendingRegenerations.set(conversation.id, intent)
    const context = conversationScope.replace()
    const isCurrent = () => context.isCurrent() && identity.value?.id === owner
    sending.value = true
    error.value = ''
    try {
      const admission = await regenerateMessage(
        conversation.id,
        intent.messageId,
        intent.clientRequestId,
        context.signal
      )
      if (!isCurrent()) return
      pendingRegenerations.delete(conversation.id)
      await acceptMainRun(admission, context, false)
    } catch (reason) {
      if (isCurrent() && !isAborted(reason)) {
        if (isDefiniteRejection(reason) && !previous) pendingRegenerations.delete(conversation.id)
        error.value = `${errorMessage(reason)}${pendingRegenerations.has(conversation.id) ? '；重新生成结果待确认，请使用原请求重试。' : ''}`
        disconnected.value = running.value
      }
    } finally {
      if (isCurrent()) sending.value = false
    }
  }

  async function retryRegeneration(): Promise<void> {
    const pending = selected.value ? pendingRegenerations.get(selected.value.id) : undefined
    const message = pending
      ? messages.value.find((item) => item.id === pending.messageId)
      : undefined
    if (message) await regenerate(message)
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault()
      void send()
    }
  }
  function resetSelection(): void {
    dismissInputIssue()
    canRetry.value = pendingSends.has(pendingKey())
    sending.value = false
  }
  function accepted(conversationId: string): void {
    pendingSends.delete(conversationId)
    pendingRegenerations.delete(conversationId)
  }
  function reconcileHistory(conversationId: string, history: ChatMessage[]): void {
    const pending = pendingRegenerations.get(conversationId)
    if (
      pending &&
      history.some(
        (message) =>
          message.role === 'assistant' &&
          message.generation_index > pending.generationIndex &&
          (pending.questionId === null || message.reply_to_message_id === pending.questionId)
      )
    )
      pendingRegenerations.delete(conversationId)
  }
  const hasPendingRegeneration = (id: string) => pendingRegenerations.has(id)
  onBeforeUnmount(() => {
    disposed = true
    pendingSends.clear()
    resetSelection()
    pendingRegenerations.clear()
  })
  return {
    sending,
    canRetry,
    inputIssue,
    dismissInputIssue,
    regenerationPending,
    canRetryRegeneration,
    send,
    regenerate,
    retryRegeneration,
    regenerationReason,
    keydown,
    resetSelection,
    accepted,
    reconcileHistory,
    hasPendingRegeneration,
    hasPendingSend: (id: string) => pendingSends.has(id)
  }
}
