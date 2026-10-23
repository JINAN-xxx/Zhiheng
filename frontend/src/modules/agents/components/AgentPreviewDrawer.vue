<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <a-drawer
    :open="modelValue"
    :closable="!sending || !!currentRun"
    :mask-closable="!sending || !!currentRun"
    :keyboard="!sending || !!currentRun"
    width="min(680px, 100vw)"
    :body-style="{ display: 'flex', flexDirection: 'column', padding: '0', minHeight: '0' }"
    @close="close"
  >
    <template #title
      ><span class="agent-drawer-title">{{ agent?.name || '智能体' }} · 试聊</span></template
    >
    <template #extra
      ><a-tooltip title="清空对话"
        ><a-button
          type="text"
          aria-label="清空对话"
          :disabled="(!messages.length && !error) || (sending && !currentRun)"
          @click="clearConversation"
          ><template #icon><ClearOutlined /></template></a-button></a-tooltip
    ></template>
    <div class="preview-version">已保存草稿 v{{ agent?.version }}</div>
    <div ref="historyRef" class="preview-history" aria-live="polite" :aria-busy="sending">
      <article
        v-if="welcomeMessage"
        class="preview-welcome preview-message preview-message--assistant"
      >
        <div class="preview-message__label">
          <RobotOutlined /><strong>{{ agent?.name }}</strong>
        </div>
        <div class="preview-message__content">{{ welcomeMessage }}</div>
      </article>
      <WorkspaceState v-else-if="!messages.length && !sending" kind="empty" title="暂无消息" />
      <article
        v-for="(item, index) in messages"
        :key="index"
        class="preview-message"
        :class="`preview-message--${item.role}`"
      >
        <div class="preview-message__label">
          <UserOutlined v-if="item.role === 'user'" /><RobotOutlined v-else /><strong>{{
            item.role === 'user' ? '我' : agent?.name
          }}</strong>
        </div>
        <details
          v-if="item.reasoning"
          class="preview-reasoning"
          :open="sending && index === messages.length - 1"
        >
          <summary>思考过程</summary>
          <div>{{ item.reasoning }}</div>
        </details>
        <div v-if="item.content" class="preview-message__content">{{ item.content }}</div>
        <div v-if="item.status === 'cancelled'" class="preview-message__status">已停止</div>
      </article>
      <div v-if="sending" class="preview-pending">
        <a-spin size="small" /><span>{{ phase }}</span>
      </div>
      <a-alert v-if="error" class="preview-error" type="error" :message="error" show-icon />
      <AButton v-if="recoverable" @click="recover">恢复查看</AButton>
      <a-collapse v-if="completion" class="preview-json" ghost
        ><a-collapse-panel key="json" header="JSON 响应">
          <pre>{{ JSON.stringify(completion, null, 2) }}</pre>
        </a-collapse-panel></a-collapse
      >
    </div>
    <div class="preview-composer">
      <a-textarea
        v-model:value="draft"
        aria-label="试聊消息"
        placeholder="输入消息"
        :auto-size="{ minRows: 3, maxRows: 7 }"
        :maxlength="32000"
        :disabled="sending"
        @keydown.enter.exact.prevent="send"
      />
      <div class="preview-composer__actions">
        <a-tooltip v-if="sending" title="停止生成"
          ><a-button aria-label="停止生成" :loading="stopping" @click="cancel"
            ><template #icon><StopOutlined /></template></a-button
        ></a-tooltip>
        <a-button
          v-else
          type="primary"
          :disabled="!draft.trim() || !agent?.agentId || recoverable || stopping"
          aria-label="发送"
          @click="send"
          ><template #icon><SendOutlined /></template>发送</a-button
        >
      </div>
    </div>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Collapse as ACollapse,
  CollapsePanel as ACollapsePanel,
  Drawer as ADrawer,
  Spin as ASpin,
  Textarea as ATextarea,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  ClearOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  UserOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { admitPreview, cancelRun, getRun, nextPreviewIntent, observeRun } from '../api/index.ts'
import type { Agent, PreviewIntent, PreviewMessage } from '../api/index.ts'
const props = defineProps<{ modelValue: boolean; agent: Agent | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
interface Message extends PreviewMessage {
  reasoning?: string
  status?: string
}
const welcomeMessage = computed(() => props.agent?.welcomeMessage.trim() ?? '')
const messages = ref<Message[]>([]),
  draft = ref(''),
  sending = ref(false),
  error = ref(''),
  completion = ref<RunSnapshot | null>(null),
  phase = ref('正在准备对话'),
  historyRef = ref<HTMLElement>(),
  currentRun = ref<RunSnapshot | null>(null),
  recoverable = ref(false),
  stopping = ref(false)
const scope = new RequestScope()
let pending: PreviewIntent | null = null
let assistant: Message | null = null
let stopRequested = false
let cancelPending: Promise<void> | null = null
async function scroll(): Promise<void> {
  await nextTick()
  historyRef.value?.scrollTo({ top: historyRef.value.scrollHeight, behavior: 'smooth' })
}
function project(run: RunSnapshot): void {
  if (
    currentRun.value &&
    (run.id !== currentRun.value.id || !acceptSnapshot(currentRun.value, run))
  )
    return
  currentRun.value = run
  if (assistant) {
    assistant.content = run.text
    assistant.reasoning = run.reasoning ?? ''
    assistant.status = run.status
  }
  phase.value =
    run.reasoning && !run.text
      ? '正在思考'
      : run.status === 'pending'
        ? '正在等待模型输出'
        : '正在生成回答'
  if (isTerminal(run)) {
    completion.value = run
    if (run.error) error.value = run.error.message
    else if (run.status === 'completed' && !run.text.trim())
      error.value = '模型未返回回答正文，请重试或调整模型设置'
    recoverable.value = false
  }
  void scroll()
}
async function follow(
  run: RunSnapshot,
  context: ReturnType<RequestScope['current']>
): Promise<void> {
  project(run)
  if (stopRequested && !isTerminal(run)) {
    await cancel()
    if (!context.isCurrent()) return
    run = currentRun.value ?? run
  }
  await observeRun(
    run,
    (snapshot) => {
      if (context.isCurrent()) project(snapshot)
    },
    context.signal
  )
}
async function send(event?: Event): Promise<void> {
  if (event instanceof KeyboardEvent && event.isComposing) return
  const agent = props.agent,
    content = draft.value.trim()
  if (!agent || sending.value || cancelPending || recoverable.value || !content) return
  const priorMessages = messages.value.map((item) => ({ ...item }))
  const conversation: Message[] = [...priorMessages, { role: 'user', content }]
  const payload = conversation
    .filter((item) => item.content.trim())
    .map((item) => ({ role: item.role, content: item.content }))
  const intent = nextPreviewIntent(pending, agent.agentId, payload, () => crypto.randomUUID())
  pending = intent
  const context = scope.replace()
  cancelPending = null
  messages.value = [
    ...conversation,
    { role: 'assistant', content: '', reasoning: '', status: 'pending' }
  ]
  assistant = messages.value.at(-1) ?? null
  currentRun.value = null
  completion.value = null
  error.value = ''
  recoverable.value = false
  stopRequested = false
  stopping.value = false
  sending.value = true
  phase.value = '正在准备对话'
  draft.value = ''
  void scroll()
  let admitted = false
  try {
    const run = await admitPreview(
      agent.agentId,
      { messages: intent.messages, client_request_id: intent.clientRequestId },
      context.signal
    )
    if (!context.isCurrent()) return
    admitted = true
    pending = null
    await follow(run, context)
  } catch (reason) {
    if (!context.isCurrent() || isAborted(reason)) return
    error.value = errorMessage(reason)
    if (!admitted) {
      messages.value = priorMessages
      draft.value = content
      assistant = null
    } else recoverable.value = currentRun.value !== null && !isTerminal(currentRun.value)
  } finally {
    if (context.isCurrent()) {
      sending.value = false
      if (!cancelPending) stopping.value = false
      void scroll()
    }
  }
}
function cancel(): Promise<void> {
  stopRequested = true
  if (cancelPending) return cancelPending
  const run = currentRun.value
  if (!run || isTerminal(run)) return Promise.resolve()
  const context = scope.current()
  stopping.value = true
  const operation = (async () => {
    try {
      const next = await cancelRun(run.id, context.signal)
      if (context.isCurrent()) {
        error.value = ''
        recoverable.value = false
        project(next)
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) {
        error.value = errorMessage(reason)
        recoverable.value = true
      }
    } finally {
      if (context.isCurrent()) {
        stopping.value = false
        cancelPending = null
      }
    }
  })()
  cancelPending = operation
  return operation
}
async function recover(): Promise<void> {
  const owner = scope.current()
  if (cancelPending) await cancelPending
  if (!owner.isCurrent()) return
  const run = currentRun.value
  if (!run || sending.value) return
  const context = scope.replace()
  sending.value = true
  error.value = ''
  recoverable.value = false
  try {
    const next = await getRun(run.id, context.signal)
    if (context.isCurrent()) await follow(next, context)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      recoverable.value = true
    }
  } finally {
    if (context.isCurrent()) sending.value = false
  }
}
function reset(): void {
  scope.replace()
  messages.value = []
  draft.value = ''
  error.value = ''
  completion.value = null
  currentRun.value = null
  pending = null
  assistant = null
  sending.value = false
  stopping.value = false
  stopRequested = false
  cancelPending = null
  recoverable.value = false
}
async function clearConversation(): Promise<void> {
  const context = scope.current()
  if (cancelPending || (currentRun.value && !isTerminal(currentRun.value))) {
    await cancel()
    if (error.value && recoverable.value) return
  }
  if (context.isCurrent()) reset()
}
async function close(): Promise<void> {
  const context = scope.current()
  if (cancelPending || (currentRun.value && !isTerminal(currentRun.value))) {
    await cancel()
    if (error.value && recoverable.value) return
  }
  if (!context.isCurrent()) return
  reset()
  emit('update:modelValue', false)
}
watch(() => [props.modelValue, props.agent?.agentId, props.agent?.version], reset)
onBeforeUnmount(() => {
  scope.dispose()
  pending = null
  assistant = null
})
</script>

<style scoped>
.agent-drawer-title {
  display: block;
  overflow-wrap: anywhere;
}
.preview-version {
  padding: 10px 24px;
  border-bottom: 1px solid var(--gray-150);
  color: var(--color-text-secondary);
  font-size: 12px;
}
.preview-history {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
.preview-message {
  padding-bottom: 24px;
}
.preview-message__label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 9px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.preview-message__label strong {
  min-width: 0;
  overflow-wrap: anywhere;
}
.preview-message--assistant .preview-message__label {
  color: var(--main-700);
}
.preview-message__content {
  padding-left: 22px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--color-text);
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
}
.preview-reasoning {
  margin: 0 0 12px 22px;
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-body);
}
.preview-reasoning summary {
  cursor: pointer;
}
.preview-reasoning div {
  padding-top: 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: var(--workspace-line-height);
}
.preview-message__status {
  padding: 8px 0 0 22px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.preview-pending {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 20px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.preview-error {
  margin-bottom: 16px;
}
.preview-json pre {
  max-height: 250px;
  overflow: auto;
  padding: 12px;
  background: var(--gray-50);
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  font-size: 12px;
}
.preview-composer {
  flex: 0 0 auto;
  padding: 16px 24px;
  border-top: 1px solid var(--gray-150);
}
.preview-composer__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}
@media (max-width: 520px) {
  .preview-history {
    padding: 16px;
  }
  .preview-composer {
    padding: 12px 16px;
  }
}
</style>
