import { computed, reactive, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { runStatusLabel } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { groupMessageVersions } from './messageVersions.ts'

interface MessagePresentationOptions {
  messages: Ref<ChatMessage[]>
  activeRun: Ref<RunSnapshot | null>
  selected: Ref<Conversation | null>
  regenerate: (message: ChatMessage) => Promise<void>
}
export function useChatMessagePresentation({
  messages,
  activeRun,
  selected,
  regenerate
}: MessagePresentationOptions) {
  const selectedVersions = reactive<Record<string, string>>({})
  const messageRows = computed(() =>
    groupMessageVersions(messages.value).map((row) => {
      const index = Math.max(
        0,
        row.versions.findIndex((item) => item.id === selectedVersions[row.key])
      )
      const selectedIndex =
        selectedVersions[row.key] && row.versions[index]?.id === selectedVersions[row.key]
          ? index
          : row.versions.length - 1
      return { ...row, index: selectedIndex, message: row.versions[selectedIndex]! }
    })
  )
  const activeRunRepresented = computed(() =>
    messages.value.some(
      (message) => message.role === 'assistant' && message.run_id === activeRun.value?.id
    )
  )
  const unansweredQuestion = computed(() => {
    if (
      !activeRun.value ||
      activeRunRepresented.value ||
      !['failed', 'cancelled', 'interrupted'].includes(activeRun.value.status)
    )
      return null
    const question = [...messages.value].reverse().find((message) => message.role === 'user')
    return question?.run_id === activeRun.value.id ? question : null
  })
  const replyStatus = computed(() =>
    activeRun.value?.status === 'failed'
      ? '生成失败'
      : activeRun.value
        ? runStatusLabel[activeRun.value.status]
        : ''
  )
  const showRunStatus = computed(
    () =>
      activeRun.value !== null &&
      activeRun.value.status !== 'running' &&
      activeRun.value.status !== 'completed'
  )
  const shareMessageId = ref<string | null>(null)
  function isActive(message: ChatMessage): boolean {
    return Boolean(
      message.role === 'assistant' && activeRun.value && message.run_id === activeRun.value.id
    )
  }
  function messageText(message: ChatMessage): string {
    return isActive(message) ? activeRun.value!.text : message.content
  }
  async function regenerateReply(message: ChatMessage, key: string): Promise<void> {
    delete selectedVersions[key]
    await regenerate(message)
  }
  watch(
    () => selected.value?.id,
    () => {
      shareMessageId.value = null
      for (const key of Object.keys(selectedVersions)) delete selectedVersions[key]
    }
  )
  return {
    selectedVersions,
    messageRows,
    activeRunRepresented,
    unansweredQuestion,
    replyStatus,
    showRunStatus,
    shareMessageId,
    isActive,
    messageText,
    regenerateReply
  }
}
