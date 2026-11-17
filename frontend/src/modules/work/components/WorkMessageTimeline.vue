<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import MessageActions from '../../../shared/components/MessageActions.vue'
import { AiMessage, ChatEmptyState } from '../../chat/public.ts'
import { KnowledgeSources } from '../../knowledge/public.ts'
import type { WorkMessage } from '../api/index.ts'
import { runStatusLabel } from '../../../shared/stream.ts'

const props = defineProps<{ messages: WorkMessage[]; legacy?: boolean; showProgress?: boolean }>()
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
      .join('|'),
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
      <div class="message-body">
        <div
          v-if="legacy && message.role === 'assistant' && message.agentNameSnapshot"
          class="message-meta"
        >
          {{ message.agentNameSnapshot }}
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
            :content="message.content"
            :reasoning-content="message.reasoningContent"
            :loading="['pending', 'running', 'cancel_requested'].includes(message.runStatus)"
          />
          <KnowledgeSources :sources="message.knowledgeSources" />
          <p v-if="message.errorSnapshot" class="run-error">{{ message.errorSnapshot.message }}</p>
          <slot v-if="index === lastAssistant" name="artifacts" />
        </template>
        <p v-else class="user-message">{{ message.content }}</p>
        <MessageActions :content="message.content" :role="message.role" />
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
</style>
