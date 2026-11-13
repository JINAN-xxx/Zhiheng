<!-- Adapted from original Work UI, MIT licensed. -->
<template>
  <section ref="root" class="work-timeline workspace-scroll-area" aria-label="共享工作时间线">
    <article
      v-for="message in messages"
      :key="message.messageId"
      class="work-message"
      :class="message.role"
    >
      <div class="work-message__meta">
        <strong>{{
          message.role === 'user' ? '你' : message.agentNameSnapshot || '智能体'
        }}</strong>
        <a-tag v-if="showStatus(message.runStatus)" :color="statusColor(message.runStatus)">{{
          statusLabel(message.runStatus)
        }}</a-tag>
        <time>{{ formatTime(message.createdAt) }}</time>
      </div>
      <p>{{ message.content }}</p>
      <KnowledgeSources :sources="message.knowledgeSources" />
      <details v-if="message.reasoningContent" class="work-message__reasoning">
        <summary>查看推理过程</summary>
        <p>{{ message.reasoningContent }}</p>
      </details>
      <p v-if="message.errorSnapshot" class="work-message__error">
        {{ errorText(message.errorSnapshot) }}
      </p>
      <MessageActions :content="message.content" :role="message.role" />
    </article>
    <a-empty v-if="!messages.length" description="暂无工作消息" />
  </section>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import MessageActions from '../../../shared/components/MessageActions.vue'
import { Empty as AEmpty, Tag as ATag } from 'ant-design-vue'
import { KnowledgeSources } from '../../knowledge/public.ts'
import type { WorkMessage } from '../api/index.ts'
import type { RunSnapshot, RunStatus } from '../../../shared/stream.ts'
import { runStatusLabel } from '../../../shared/stream.ts'
const props = defineProps<{ messages: WorkMessage[] }>()
const root = ref<HTMLElement | null>(null)
const showStatus = (status: RunStatus) => status !== 'completed'
const statusLabel = (status: RunStatus) =>
  status === 'cancelled' ? '已取消' : runStatusLabel[status]
const statusColor = (status: RunStatus) =>
  status === 'failed'
    ? 'error'
    : status === 'running'
      ? 'processing'
      : status === 'cancel_requested'
        ? 'gold'
        : 'default'
const errorText = (error: NonNullable<RunSnapshot['error']>) => error.message
const formatTime = (value: string) =>
  Number.isNaN(new Date(value).getTime())
    ? ''
    : new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      }).format(new Date(value))
watch(
  () =>
    props.messages
      .map((item) => `${item.messageId}:${item.content.length}:${item.reasoningContent.length}`)
      .join('|'),
  async () => {
    await nextTick()
    if (root.value) root.value.scrollTop = root.value.scrollHeight
  },
  { immediate: true }
)
</script>

<style scoped>
.work-timeline {
  min-height: 0;
  width: min(860px, 100%);
  flex: 1;
  align-self: center;
  overflow: hidden auto;
  padding: 24px 20px 32px;
}
.work-message {
  width: min(760px, 100%);
  margin: 0 0 24px;
}
.work-message.user {
  width: fit-content;
  max-width: 88%;
  margin-left: auto;
}
.work-message__meta {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.work-message__meta strong {
  min-width: 0;
  overflow: hidden;
  color: var(--color-text);
  font-weight: var(--workspace-font-weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-message__meta time {
  margin-left: auto;
}
.work-message > p {
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: var(--bg-container);
  color: var(--color-text);
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.work-message.user > p {
  padding: 11px 16px;
  border: 0;
  border-radius: 10px 0 10px 10px;
  background: var(--gray-50);
}
.work-message__reasoning {
  margin: 7px 0 0;
  padding: 9px 12px;
  border-left: 2px solid var(--gray-200);
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-body);
  line-height: var(--workspace-line-height);
}
.work-message__reasoning summary {
  cursor: pointer;
  font-size: 13px;
}
.work-message__reasoning p {
  margin: 8px 0 0;
  white-space: pre-wrap;
}
.work-message > .work-message__error {
  margin-top: 6px;
  border-color: var(--workspace-danger);
  color: var(--workspace-danger);
  font-size: 13px;
  line-height: 1.7;
}
@media (max-width: 600px) {
  .work-timeline {
    padding: 16px 12px 24px;
  }
  .work-message > p {
    padding: 12px 14px;
  }
  .work-message.user {
    max-width: 94%;
  }
}
.work-message.user :deep(.message-actions) {
  justify-content: flex-end;
}
</style>
