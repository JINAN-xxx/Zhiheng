<template>
  <section ref="root" class="work-timeline" aria-label="共享工作时间线">
    <article v-for="message in messages" :key="message.messageId" class="work-message" :class="message.role">
      <div class="work-message__meta">
        <strong>{{ message.role === 'user' ? '你' : (message.agentNameSnapshot || '智能体') }}</strong>
        <span v-if="message.role === 'assistant' && message.publishedVersion" class="work-message__version">v{{ message.publishedVersion }}</span>
        <a-tag v-if="showStatus(message.runStatus)" :color="statusColor(message.runStatus)">{{ statusLabel(message.runStatus) }}</a-tag>
        <time>{{ formatTime(message.createdAt) }}</time>
      </div>
      <p>{{ message.content }}</p>
      <details v-if="message.reasoningContent" class="work-message__reasoning">
        <summary>查看推理过程</summary>
        <p>{{ message.reasoningContent }}</p>
      </details>
      <p v-if="message.errorSnapshot" class="work-message__error">{{ errorText(message.errorSnapshot) }}</p>
    </article>
    <a-empty v-if="!messages.length" description="暂无工作消息" />
  </section>
</template>

<script setup>
const props = defineProps({ messages: { type: Array, default: () => [] } })
const root = ref(null)
const showStatus = (status) => ['failed', 'cancelled', 'interrupted'].includes(status)
const statusLabel = (status) => ({ failed: '失败', cancelled: '已取消', interrupted: '已中断' }[status] || status)
const statusColor = (status) => status === 'failed' ? 'error' : 'default'
const errorText = (error) => error.message || error.msg || error.detail || '执行失败'
const formatTime = (value) => value ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : ''
watch(() => props.messages.map((item) => `${item.messageId}:${item.content?.length || 0}`).join('|'), async () => {
  await nextTick()
  if (root.value) root.value.scrollTop = root.value.scrollHeight
}, { immediate: true })
</script>

<style scoped>
.work-timeline { min-height: 0; width: min(920px, 100%); flex: 1; align-self: center; overflow-y: auto; padding: 24px 20px 32px; }
.work-message { width: min(760px, 100%); margin: 0 0 24px; }
.work-message.user { width: fit-content; max-width: 88%; margin-left: auto; }
.work-message__meta { min-width: 0; display: flex; align-items: center; gap: 8px; margin-bottom: 8px; color: var(--color-text-secondary); font-size: 12px; }
.work-message__meta strong { min-width: 0; overflow: hidden; color: var(--color-text); text-overflow: ellipsis; white-space: nowrap; }
.work-message__meta time { margin-left: auto; }
.work-message > p { margin: 0; padding: 16px 18px; border: 1px solid var(--gray-150); border-radius: 4px 16px 16px 16px; background: var(--bg-container); color: var(--color-text); font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; white-space: pre-wrap; }
.work-message.user > p { border-color: var(--main-100); border-radius: 16px 4px 16px 16px; background: var(--main-50); }
.work-message__version { padding: 1px 5px; border-radius: 5px; background: var(--gray-100); color: var(--color-text-secondary); font-size: 11px; }
.work-message__reasoning { margin: 7px 0 0; padding: 9px 12px; border-left: 2px solid var(--gray-200); color: var(--color-text-secondary); font-size: 13px; }
.work-message__reasoning summary { cursor: pointer; }
.work-message__reasoning p { margin: 8px 0 0; white-space: pre-wrap; }
.work-message > .work-message__error { margin-top: 6px; border-color: var(--workspace-danger); color: var(--workspace-danger); }
@media (max-width: 600px) { .work-timeline { padding: 16px 12px 24px; } .work-message > p { padding: 12px 14px; } .work-message.user { max-width: 94%; } }
</style>
