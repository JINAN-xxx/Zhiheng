<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import MessageActions from '../../../shared/components/MessageActions.vue'
import AttachmentCard from '../../../shared/components/AttachmentCard.vue'
import { AiMessage, ChatEmptyState } from '../../chat/public.ts'
import { formatFileSize } from '../../files/public.ts'
import { formatKnowledgeCitations } from '../../knowledge/public.ts'
import type { WorkArtifact, WorkInput, WorkMessage } from '../api/index.ts'
import { runStatusLabel } from '../../../shared/stream.ts'

import { artifactsByRun, formatArtifactLinks } from '../artifacts.ts'
import WorkMessageArtifacts from './WorkMessageArtifacts.vue'

const props = withDefaults(
  defineProps<{
    taskId: string
    messages: WorkMessage[]
    inputs: WorkInput[]
    artifacts: WorkArtifact[]
    legacy?: boolean
    showProgress?: boolean
  }>(),
  { legacy: false, showProgress: false }
)
const emit = defineEmits<{ preview: [artifact: WorkArtifact]; 'open-input': [] }>()
const inputsById = computed(() => new Map(props.inputs.map((input) => [input.fileId, input])))
function messageInputs(message: WorkMessage): WorkInput[] {
  return (message.inputFileIds ?? []).flatMap((id) => inputsById.value.get(id) ?? [])
}
const linkPrefix = `#work-artifact-${useId().replace(/[^a-z0-9_-]/gi, '')}-`
const byRun = computed(() => artifactsByRun(props.artifacts))
const availableFiles = computed(() => {
  const runs = new Set<string>(),
    result = new Map<string, WorkArtifact[]>()
  for (const message of props.messages) {
    if (message.runId) runs.add(message.runId)
    result.set(
      message.messageId,
      props.artifacts.filter((item) => runs.has(item.runId))
    )
  }
  return result
})
function followArtifact(event: MouseEvent, message: WorkMessage): void {
  const anchor = event.target instanceof Element ? event.target.closest('a') : null
  const href = anchor?.getAttribute('href') ?? ''
  if (!href.startsWith(linkPrefix)) return
  const item = availableFiles.value
    .get(message.messageId)
    ?.find((item) => `${linkPrefix}${encodeURIComponent(item.id)}` === href)
  if (!item) return
  event.preventDefault()
  event.stopPropagation()
  emit('preview', item)
}
const root = ref<HTMLElement | null>(null)
const lastAssistant = computed(() => {
  for (let index = props.messages.length - 1; index >= 0; index--) {
    if (props.messages[index]?.role === 'assistant') return index
  }
  return -1
})
function status(message: WorkMessage): string {
  return message.runStatus === 'cancelled' ? '已取消' : runStatusLabel[message.runStatus]
}
watch(
  () =>
    props.messages
      .map(
        (item) =>
          `${item.messageId}:${item.content.length}:${item.reasoningContent.length}:${item.runStatus}`
      )
      .join('|') + props.artifacts.map((item) => item.id).join('|'),
  async () => {
    await nextTick()
    if (root.value) root.value.scrollTop = root.value.scrollHeight
  },
  { immediate: true }
)
</script>

<template>
  <section
    ref="root"
    class="message-history work-timeline workspace-scroll-area"
    :class="{ 'is-empty': !messages.length && !showProgress }"
    aria-label="工作对话"
    aria-live="polite"
  >
    <article
      v-for="(message, index) in messages"
      :key="message.messageId"
      class="message-row work-message"
      :class="message.role"
    >
      <div class="message-body" @click.capture="followArtifact($event, message)">
        <div
          v-if="legacy && message.role === 'assistant' && message.agentNameSnapshot"
          class="message-meta"
        >
          {{ message.agentNameSnapshot }}
        </div>
        <div v-if="message.inputFileIds?.length" class="work-message__inputs" aria-label="消息附件">
          <AttachmentCard
            v-for="file in messageInputs(message)"
            :key="file.fileId"
            :filename="file.name"
            :size-label="formatFileSize(file.size)"
            :mime="file.mime"
            :removable="false"
            openable
            @open="emit('open-input')"
          />
        </div>
        <template v-if="message.role === 'assistant'">
          <slot v-if="showProgress && index === lastAssistant" name="progress" />
          <div
            v-if="
              message.runStatus !== 'completed' &&
              (!showProgress || !['pending', 'running'].includes(message.runStatus))
            "
            class="message-meta"
          >
            <span class="message-run-status" :class="{ 'is-failed': !!message.errorSnapshot }">{{
              status(message)
            }}</span>
          </div>
          <AiMessage
            :content="
              formatArtifactLinks(
                message.content,
                availableFiles.get(message.messageId) ?? [],
                taskId,
                linkPrefix
              )
            "
            :sources="message.knowledgeSources"
            :reasoning-content="message.reasoningContent"
            :loading="['pending', 'running', 'cancel_requested'].includes(message.runStatus)"
          />
          <p v-if="message.errorSnapshot" class="run-error">{{ message.errorSnapshot.message }}</p>
          <WorkMessageArtifacts
            v-if="!legacy && message.runId"
            :task-id="taskId"
            :artifacts="byRun.get(message.runId) ?? []"
            @preview="emit('preview', $event)"
          />
        </template>
        <p v-else class="user-message">{{ message.content }}</p>
        <MessageActions
          :content="
            message.role === 'assistant'
              ? formatKnowledgeCitations(message.content, message.knowledgeSources)
              : message.content
          "
          :role="message.role"
        />
      </div>
    </article>
    <article v-if="showProgress && lastAssistant < 0" class="message-row assistant">
      <div class="message-body"><slot name="progress" /></div>
    </article>
    <ChatEmptyState
      v-if="!messages.length && !showProgress"
      title="有什么可以帮你？"
      description="资料准备好后，描述目标即可开始工作。"
      :show-starters="false"
    />
  </section>
</template>

<style scoped lang="scss">
@use '../../../shared/styles/conversation' as conversation;
@include conversation.history;
@include conversation.messages;
.message-history.is-empty {
  display: flex;
  align-items: center;
  justify-content: center;
}
.message-row.user :deep(.message-actions) {
  justify-content: flex-end;
}
.work-message__inputs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.message-row.user .work-message__inputs {
  justify-content: flex-end;
}
</style>
