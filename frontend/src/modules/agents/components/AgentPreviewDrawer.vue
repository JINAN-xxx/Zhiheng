<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <component
    :is="embedded ? 'section' : ADrawer"
    :class="{ 'agent-preview-panel': embedded }"
    v-bind="
      embedded
        ? {}
        : {
            open: modelValue,
            closable: false,
            maskClosable: !sending || !!currentRun || !!currentPreparation,
            keyboard: !sending || !!currentRun || !!currentPreparation,
            width: 'min(680px, 100vw)',
            bodyStyle: { display: 'flex', flexDirection: 'column', padding: '0', minHeight: '0' }
          }
    "
    @close="close"
  >
    <header class="preview-heading">
      <h2><MessageOutlined />{{ embedded ? '对话体验' : `${agent?.name || '智能体'} · 试聊` }}</h2>
      <a-tooltip title="清空对话"
        ><a-button
          type="text"
          aria-label="清空对话"
          :disabled="
            (!messages.length && !error) || (sending && !currentRun && !currentPreparation)
          "
          @click="clearConversation"
          ><template #icon><ClearOutlined /></template></a-button
      ></a-tooltip>
      <a-button
        v-if="!embedded"
        type="text"
        aria-label="关闭试聊"
        :disabled="sending && !currentRun && !currentPreparation"
        @click="close"
        ><CloseOutlined
      /></a-button>
    </header>
    <div class="preview-version">
      <RobotOutlined /><span>{{ agent ? `已保存草稿 v${agent.version}` : '保存后可开始试聊' }}</span
      ><span v-if="disabled && agent" class="preview-save-hint">保存后试聊</span>
    </div>
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
      <WorkspaceState
        v-else-if="!messages.length && !sending"
        kind="empty"
        :title="agent?.name || '对话体验'"
        :message="agent ? '发送消息，体验已保存草稿。' : '保存智能体后可开始对话。'"
      />
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
        <KnowledgeSources v-if="item.sources" :sources="item.sources" />
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
        :disabled="sending || disabled"
        @keydown.enter.exact.prevent="send"
      />
      <div class="preview-composer__actions">
        <span>Enter 发送 · Shift + Enter 换行</span>
        <a-tooltip v-if="sending" title="停止生成"
          ><a-button
            aria-label="停止生成"
            :loading="stopping"
            :disabled="stopAcknowledged"
            @click="cancel"
            ><template #icon><StopOutlined /></template></a-button
        ></a-tooltip>
        <a-button
          v-else
          type="primary"
          :disabled="disabled || !draft.trim() || !agent?.agentId || recoverable || stopping"
          aria-label="发送"
          @click="send"
          ><template #icon><SendOutlined /></template>发送</a-button
        >
      </div>
    </div>
  </component>
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
  CloseOutlined,
  MessageOutlined,
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
import {
  KnowledgeSources,
  cancelPreparation,
  getPreparation,
  isPreparation,
  observePreparation,
  projectPreparation as mergePreparation,
  preparationFinished
} from '../../knowledge/public.ts'
import type { KnowledgeHit, KnowledgePreparation } from '../../knowledge/public.ts'
const props = withDefaults(
  defineProps<{
    modelValue: boolean
    agent: Agent | null
    embedded?: boolean
    disabled?: boolean
  }>(),
  { embedded: false, disabled: false }
)
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'busy-change': [value: boolean]
}>()
interface Message extends PreviewMessage {
  reasoning?: string
  status?: string
  sources?: KnowledgeHit[]
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
  currentPreparation = ref<KnowledgePreparation | null>(null),
  recoverable = ref(false),
  stopping = ref(false)
const scope = new RequestScope()
let pending: PreviewIntent | null = null
let assistant: Message | null = null
let stopRequested = false
let cancelPending: Promise<void> | null = null
const stopAcknowledged = computed(
  () => (currentRun.value ?? currentPreparation.value)?.status === 'cancel_requested'
)
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
  if (run.status === 'cancel_requested' || isTerminal(run)) stopRequested = false
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
  run = currentRun.value ?? run
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
function projectPreparation(value: KnowledgePreparation): KnowledgePreparation {
  if (value.resource_type !== 'agent_preview' || value.resource_id !== props.agent?.agentId)
    throw new Error('知识检索准备与当前智能体不匹配')
  value = mergePreparation(currentPreparation.value, value)
  currentPreparation.value = value
  if (
    value.status === 'cancel_requested' ||
    (preparationFinished(value) && value.status !== 'admitted')
  )
    stopRequested = false
  phase.value = '正在检索知识库'
  if (assistant) {
    assistant.sources = value.hits
    if (value.status === 'cancelled') assistant.status = 'cancelled'
    else if (['failed', 'interrupted'].includes(value.status)) assistant.status = 'failed'
  }
  return value
}
async function followKnowledge(
  initial: KnowledgePreparation,
  context: ReturnType<RequestScope['current']>
): Promise<void> {
  initial = projectPreparation(initial)
  if (stopRequested && !preparationFinished(initial)) {
    await cancel()
    if (!context.isCurrent()) return
    initial = currentPreparation.value ?? initial
  }
  const final = await observePreparation(
    initial,
    (value) => {
      if (context.isCurrent()) return projectPreparation(value)
    },
    context.signal
  )
  if (!context.isCurrent()) return
  if (final.run) await follow(final.run, context)
  else if (final.status !== 'cancelled') error.value = final.error?.message ?? '知识检索未完成'
  recoverable.value = false
}
async function send(event?: Event): Promise<void> {
  if (event instanceof KeyboardEvent && event.isComposing) return
  const agent = props.agent,
    content = draft.value.trim()
  if (!agent || props.disabled || sending.value || cancelPending || recoverable.value || !content)
    return
  const priorMessages = messages.value.map((item) => ({ ...item }))
  const conversation: Message[] = [...priorMessages, { role: 'user', content }]
  const intent = nextPreviewIntent(
    pending,
    agent.agentId,
    conversation.map((item) => ({
      role: item.role,
      content: item.content,
      ...(item.status === undefined ? {} : { status: item.status })
    })),
    () => crypto.randomUUID(),
    agent.historyRounds
  )
  pending = intent
  const context = scope.replace()
  cancelPending = null
  messages.value = [
    ...conversation,
    { role: 'assistant', content: '', reasoning: '', status: 'pending' }
  ]
  assistant = messages.value.at(-1) ?? null
  currentRun.value = null
  currentPreparation.value = null
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
    const admission = await admitPreview(
      agent.agentId,
      { messages: intent.messages, client_request_id: intent.clientRequestId },
      context.signal
    )
    if (!context.isCurrent()) return
    admitted = true
    pending = null
    if (isPreparation(admission)) await followKnowledge(admission, context)
    else await follow(admission, context)
  } catch (reason) {
    if (!context.isCurrent() || isAborted(reason)) return
    error.value = errorMessage(reason)
    if (!admitted) {
      messages.value = priorMessages
      draft.value = content
      assistant = null
    } else
      recoverable.value =
        (currentRun.value !== null && !isTerminal(currentRun.value)) ||
        (currentPreparation.value !== null && !preparationFinished(currentPreparation.value))
  } finally {
    if (context.isCurrent()) {
      sending.value = false
      if (!cancelPending) stopping.value = false
      void scroll()
    }
  }
}
function cancel(): Promise<void> {
  if (cancelPending) return cancelPending
  if (stopAcknowledged.value) return Promise.resolve()
  stopRequested = true
  const run = currentRun.value
  const preparation = currentPreparation.value
  if ((!preparation || preparationFinished(preparation)) && (!run || isTerminal(run)))
    return Promise.resolve()
  const context = scope.current()
  stopping.value = true
  const operation = (async () => {
    try {
      if (preparation && !preparationFinished(preparation)) {
        const next = await cancelPreparation(preparation.id, context.signal)
        if (!context.isCurrent()) return
        projectPreparation(next)
        if (next.run) {
          project(next.run)
          if (!isTerminal(next.run) && next.run.status !== 'cancel_requested') {
            const stopped = await cancelRun(next.run.id, context.signal)
            if (context.isCurrent()) project(stopped)
          }
        }
      } else if (run) {
        const next = await cancelRun(run.id, context.signal)
        if (context.isCurrent()) project(next)
      }
      if (context.isCurrent()) {
        error.value = ''
        recoverable.value = false
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
  const preparation = currentPreparation.value
  if ((!run && !preparation) || sending.value) return
  const context = scope.replace()
  sending.value = true
  error.value = ''
  recoverable.value = false
  try {
    if (preparation && !preparationFinished(preparation)) {
      const next = await getPreparation(preparation.id, context.signal)
      if (context.isCurrent()) await followKnowledge(next, context)
    } else if (run) {
      const next = await getRun(run.id, context.signal)
      if (context.isCurrent()) await follow(next, context)
    }
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
  currentPreparation.value = null
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
  if (
    cancelPending ||
    (currentRun.value && !isTerminal(currentRun.value)) ||
    (currentPreparation.value && !preparationFinished(currentPreparation.value))
  ) {
    await cancel()
    if (error.value && recoverable.value) return
  }
  if (context.isCurrent()) reset()
}
async function close(): Promise<void> {
  const context = scope.current()
  if (
    cancelPending ||
    (currentRun.value && !isTerminal(currentRun.value)) ||
    (currentPreparation.value && !preparationFinished(currentPreparation.value))
  ) {
    await cancel()
    if (error.value && recoverable.value) return
  }
  if (!context.isCurrent()) return
  reset()
  emit('update:modelValue', false)
}
watch(sending, (value) => emit('busy-change', value))
watch([() => props.modelValue, () => props.agent?.agentId], reset)
onBeforeUnmount(() => {
  emit('busy-change', false)
  scope.dispose()
  pending = null
  assistant = null
})
</script>

<style scoped>
.agent-preview-panel {
  display: flex;
  min-width: 0;
  height: min(650px, calc(100dvh - 230px));
  min-height: 440px;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: #fff;
}
.preview-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--gray-100);
}
.preview-heading > .ant-tooltip-disabled-compatible-wrapper,
.preview-heading > .ant-btn {
  flex: 0 0 auto;
}
.preview-heading h2 {
  margin-right: auto;
  display: flex;
  gap: 9px;
  align-items: center;
  margin: 0;
  font-size: 16px;
  font-weight: 500;
}
.preview-heading h2 > span {
  color: var(--main-700);
}
.preview-version {
  display: flex;
  align-items: center;
  gap: 7px;
  background: var(--gray-25);
}
.preview-save-hint {
  margin-left: auto;
  color: var(--main-700);
}
.agent-preview-panel .preview-history {
  padding: 20px 16px;
}
.agent-preview-panel .preview-composer {
  margin: 0 12px 12px;
  padding: 10px;
  border: 1px solid var(--gray-150);
  border-radius: 7px;
}
.agent-preview-panel .preview-composer :deep(textarea) {
  border: 0;
  box-shadow: none;
  padding: 0;
  font-size: 14px;
}
.agent-preview-panel .preview-message__content {
  margin-left: 22px;
  padding: 10px 12px;
  border-radius: 0 7px 7px 7px;
  background: var(--gray-50);
  font-size: 14px;
}
.agent-preview-panel .preview-message--user .preview-message__content {
  background: var(--main-50);
}
.preview-composer__actions > span {
  margin-right: auto;
  align-self: center;
  color: var(--color-text-secondary);
  font-size: 11px;
}

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
