<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Empty as AEmpty,
  Select as ASelect,
  Spin as ASpin,
  Tag as ATag,
  Textarea as ATextarea
} from 'ant-design-vue'
import {
  ArrowUpOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined
} from '@ant-design/icons-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { acceptSnapshot, isTerminal, runStatusLabel } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { identity } from '../../identity/public.ts'
import { getModels } from '../../models/public.ts'
import type { ChatModel } from '../../models/public.ts'
import {
  cancelRun,
  createConversation,
  getConversations,
  getMessages,
  getRun,
  observeRun,
  sendMessage
} from '../api/index.ts'
import type { ChatMessage, Conversation } from '../api/index.ts'
import { nextSendIntent } from '../api/sendIntent.ts'
import type { SendIntent } from '../api/sendIntent.ts'
const conversations = ref<Conversation[]>([])
const models = ref<ChatModel[]>([])
const selected = ref<Conversation | null>(null)
const modelId = ref<string>()
const messages = ref<ChatMessage[]>([])
const draft = ref('')
const error = ref('')
const listError = ref('')
const loading = ref(false)
const listLoading = ref(false)
const historyReady = ref(true)
const sending = ref(false)
const stopping = ref(false)
const disconnected = ref(false)
const activeRun = ref<RunSnapshot | null>(null)
const messagesElement = ref<HTMLElement | null>(null)
const pageScope = new RequestScope()
const conversationScope = new RequestScope()
const running = computed(() => activeRun.value !== null && !isTerminal(activeRun.value))
const canRecover = computed(() => disconnected.value && running.value)
const modelOptions = computed(() =>
  models.value
    .filter((model) => model.enabled && model.capabilities.includes('chat'))
    .map((model) => ({ value: model.id, label: model.name }))
)
const visibleMessages = computed(() =>
  messages.value.filter(
    (message) => !(message.role === 'assistant' && message.run_id === activeRun.value?.id)
  )
)
let pendingSend: SendIntent | null = null
const canRetry = ref(false)
onBeforeUnmount(() => {
  pageScope.dispose()
  conversationScope.dispose()
  draft.value = ''
  pendingSend = null
})
async function scroll(): Promise<void> {
  await nextTick()
  messagesElement.value?.scrollTo({ top: messagesElement.value.scrollHeight, behavior: 'smooth' })
}
function updateRun(run: RunSnapshot): void {
  if (acceptSnapshot(activeRun.value, run)) {
    activeRun.value = run
    void scroll()
  }
}
async function follow(
  run: RunSnapshot,
  context: ReturnType<RequestScope['current']>
): Promise<void> {
  disconnected.value = false
  await observeRun(
    run,
    (snapshot) => {
      if (context.isCurrent()) updateRun(snapshot)
    },
    context.signal
  )
  if (context.isCurrent() && selected.value) {
    messages.value = await getMessages(selected.value.id, context.signal)
    if (context.isCurrent()) await scroll()
  }
}
async function selectConversation(conversation: Conversation | null): Promise<void> {
  const context = conversationScope.replace()
  selected.value = conversation
  messages.value = []
  activeRun.value = null
  error.value = ''
  draft.value = ''
  canRetry.value = false
  pendingSend = null
  disconnected.value = false
  sending.value = false
  stopping.value = false
  loading.value = false
  historyReady.value = conversation === null
  if (!conversation) return
  loading.value = true
  try {
    const history = await getMessages(conversation.id, context.signal)
    if (!context.isCurrent()) return
    messages.value = history
    const last = history.at(-1)
    if (last?.role === 'user' && last.run_id) {
      const run = await getRun(last.run_id, context.signal)
      if (!context.isCurrent()) return
      activeRun.value = run
      historyReady.value = true
      loading.value = false
      await follow(run, context)
    } else {
      historyReady.value = true
    }
    if (context.isCurrent()) await scroll()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      disconnected.value = running.value
    }
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
async function load(): Promise<void> {
  const context = pageScope.replace()
  listLoading.value = true
  listError.value = ''
  try {
    const [availableModels, items] = await Promise.all([
      getModels(context.signal),
      getConversations(context.signal)
    ])
    if (!context.isCurrent()) return
    models.value = availableModels
    conversations.value = items
    if (!modelOptions.value.some((model) => model.value === modelId.value)) {
      modelId.value = modelOptions.value[0]?.value
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) listError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) listLoading.value = false
  }
}
onMounted(() => {
  void load()
})
async function send(retry = false): Promise<void> {
  const model = modelId.value
  const message = draft.value.trim()
  if (
    sending.value ||
    running.value ||
    loading.value ||
    listLoading.value ||
    !historyReady.value ||
    (!retry && (!message || !model))
  )
    return
  const ownerId = identity.value?.id
  if (!ownerId) return
  const context = conversationScope.replace()
  const isCurrent = () => context.isCurrent() && identity.value?.id === ownerId
  const request = retry
    ? pendingSend
    : model
      ? nextSendIntent(
          pendingSend,
          {
            conversationId: selected.value?.id ?? null,
            model_id: model,
            message
          },
          () => crypto.randomUUID()
        )
      : null
  if (!request) return
  pendingSend = request
  sending.value = true
  error.value = ''
  canRetry.value = false
  try {
    let conversationId = request.conversationId
    if (conversationId === null) {
      const conversation = await createConversation(context.signal)
      if (!isCurrent()) return
      pageScope.replace()
      listLoading.value = false
      selected.value = conversation
      conversations.value.unshift(conversation)
      conversationId = conversation.id
      pendingSend = { ...request, conversationId }
    }
    const run = await sendMessage(
      conversationId,
      {
        model_id: request.model_id,
        message: request.message,
        client_request_id: request.client_request_id
      },
      context.signal
    )
    if (!isCurrent()) return
    activeRun.value = run
    pendingSend = null
    draft.value = ''
    messages.value = await getMessages(conversationId, context.signal)
    if (!isCurrent()) return
    sending.value = false
    await scroll()
    await follow(run, context)
  } catch (reason) {
    if (isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      canRetry.value = pendingSend !== null
      disconnected.value = running.value
    }
  } finally {
    if (isCurrent()) sending.value = false
  }
}
async function stop(): Promise<void> {
  const run = activeRun.value
  if (!run || stopping.value) return
  const context = conversationScope.current()
  stopping.value = true
  try {
    const result = await cancelRun(run.id, context.signal)
    if (context.isCurrent()) updateRun(result)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) stopping.value = false
  }
}
async function reconnect(): Promise<void> {
  const run = activeRun.value
  if (!run) return
  const context = conversationScope.replace()
  error.value = ''
  try {
    const latest = await getRun(run.id, context.signal)
    if (!context.isCurrent()) return
    updateRun(latest)
    await follow(latest, context)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      disconnected.value = true
    }
  }
}
function setModelId(value: unknown): void {
  modelId.value = typeof value === 'string' ? value : undefined
}
function keydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void send()
  }
}
</script>
<template>
  <div class="chat-workspace">
    <aside class="conversation-panel">
      <div class="panel-heading">
        <span>对话记录</span
        ><AButton
          type="text"
          size="small"
          aria-label="刷新会话"
          :loading="listLoading"
          @click="load"
          ><ReloadOutlined
        /></AButton>
      </div>
      <AButton class="new-conversation" block @click="selectConversation(null)"
        ><PlusOutlined /> 新建对话</AButton
      >
      <div class="conversation-list">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          :class="['conversation-item', { selected: selected?.id === conversation.id }]"
          @click="selectConversation(conversation)"
        >
          <MessageOutlined /><span>{{ conversation.title }}</span>
        </button>
        <p v-if="!conversations.length" class="muted small conversation-hint">
          你的对话将在这里留存
        </p>
      </div>
      <p class="sidebar-note">一次对话，一点新想法。</p>
    </aside>
    <section class="chat-main">
      <header class="chat-header">
        <div>
          <h1>{{ selected?.title || '新的对话' }}</h1>
          <span class="muted small">文本对话 · 流式输出</span>
        </div>
        <label class="sr-only" for="chat-model-select">选择模型</label>
        <ASelect
          id="chat-model-select"
          :value="modelId"
          class="model-select"
          :options="modelOptions"
          placeholder="选择模型"
          :disabled="running || sending"
          aria-label="选择模型"
          @update:value="setModelId"
        />
      </header>
      <div v-if="error || listError || canRetry || canRecover" class="chat-alert">
        <AAlert v-if="error" :message="error" type="error" show-icon />
        <AAlert v-if="listError" :message="listError" type="error" show-icon />
        <div class="alert-actions">
          <AButton v-if="canRetry" size="small" @click="send(true)">使用原请求重试</AButton
          ><AButton v-if="canRecover" size="small" @click="reconnect">恢复查看</AButton>
        </div>
      </div>
      <div ref="messagesElement" class="message-scroll" aria-live="polite" aria-label="聊天消息">
        <ASpin v-if="loading" class="page-spinner" />
        <div v-else-if="!messages.length && !activeRun" class="chat-welcome">
          <div class="welcome-orbit">衡</div>
          <p class="eyebrow">一点好奇，无限可能</p>
          <h2>今天，一起想点什么？</h2>
          <p class="muted">从一个问题开始，让想法慢慢展开。</p>
          <div class="welcome-prompts">
            <button @click="draft = '帮我把一个复杂的想法整理成清晰的行动计划。'">
              整理一个想法 <span>↗</span></button
            ><button @click="draft = '请用通俗的语言解释一个有趣的科学概念。'">
              学习新的知识 <span>↗</span>
            </button>
          </div>
          <AEmpty
            v-if="!modelOptions.length"
            description="暂无可用模型，请联系管理员配置"
            :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
          />
        </div>
        <template v-else
          ><article
            v-for="message in visibleMessages"
            :key="message.id"
            :class="['message', message.role]"
          >
            <div class="message-avatar">{{ message.role === 'user' ? '你' : '衡' }}</div>
            <div class="message-body">
              <div class="message-author">{{ message.role === 'user' ? '你' : '智衡 AI' }}</div>
              <div class="message-content">{{ message.content }}</div>
            </div>
          </article>
          <article v-if="activeRun" class="message assistant">
            <div class="message-avatar">衡</div>
            <div class="message-body">
              <div class="message-author">
                智衡 AI
                <ATag
                  :color="
                    running
                      ? 'processing'
                      : activeRun.status === 'completed'
                        ? 'success'
                        : 'default'
                  "
                  >{{ runStatusLabel[activeRun.status] }}</ATag
                >
              </div>
              <div class="message-content">
                {{ activeRun.text || (running ? '正在思考…' : '本次运行未生成文本') }}
              </div>
              <p v-if="activeRun.error" class="run-error">{{ activeRun.error.message }}</p>
              <span v-if="activeRun.usage.status === 'unknown'" class="muted small"
                >用量待核对</span
              >
            </div>
          </article></template
        >
      </div>
      <footer class="composer-area">
        <div class="composer">
          <ATextarea
            v-model:value="draft"
            placeholder="输入你的问题，Enter 发送，Shift + Enter 换行"
            :auto-size="{ minRows: 2, maxRows: 7 }"
            :maxlength="16000"
            :disabled="sending"
            aria-label="消息内容"
            @keydown="keydown"
          />
          <div class="composer-toolbar">
            <span class="muted small">{{
              draft.length ? `${draft.length} / 16000` : '给思考一点空间'
            }}</span
            ><AButton v-if="running" :loading="stopping" @click="stop"
              ><StopOutlined /> 停止生成</AButton
            ><AButton
              v-else
              type="primary"
              shape="circle"
              :loading="sending"
              :disabled="!draft.trim() || !modelId || loading || listLoading || !historyReady"
              aria-label="发送消息"
              @click="send()"
              ><ArrowUpOutlined
            /></AButton>
          </div>
        </div>
        <p class="composer-note">AI 生成内容仅供参考，请核实重要信息。</p>
      </footer>
    </section>
  </div>
</template>
