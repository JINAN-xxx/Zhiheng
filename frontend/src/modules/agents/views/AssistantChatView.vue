<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Textarea as ATextarea,
  Spin as ASpin
} from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  PlusOutlined,
  ArrowUpOutlined,
  BorderOutlined,
  HistoryOutlined
} from '@ant-design/icons-vue'
import { AiMessage } from '../../chat/public.ts'
import AssistantWelcome from '../components/AssistantWelcome.vue'
import AssistantHistoryDialog from '../components/AssistantHistoryDialog.vue'
import {
  KnowledgeSources,
  cancelPreparation,
  getPreparation,
  isPreparation,
  observePreparation,
  preparationFinished
} from '../../knowledge/public.ts'
import type { KnowledgePreparation } from '../../knowledge/public.ts'
import { identity, canUseCapability } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { cancelRun, observeRun } from '../../../shared/runs.ts'
import { acceptSnapshot, isTerminal, runStatusLabel } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { getAgent } from '../api/index.ts'
import type { Agent } from '../api/index.ts'
import {
  createAssistantConversation,
  getAssistantConversation,
  getAssistantConversations,
  sendAssistantMessage
} from '../api/conversations.ts'
import type { AssistantConversation, AssistantTurn } from '../api/conversations.ts'

const route = useRoute(),
  router = useRouter()
const agentId = String(route.params.id)
const agent = ref<Agent | null>(null)
const conversations = ref<AssistantConversation[]>([])
const selected = ref<AssistantConversation | null>(null)
const turns = ref<AssistantTurn[]>([])
const older = ref<number | null>(null)
const hasMore = ref(false)
const historyOpen = ref(false),
  listLoading = ref(false),
  olderLoading = ref(false)
const draft = ref(''),
  error = ref(''),
  listError = ref('')
const loading = ref(false),
  sending = ref(false),
  disconnected = ref(false),
  stopping = ref(false)
const activeRun = ref<RunSnapshot | null>(null)
const preparation = ref<KnowledgePreparation | null>(null)
interface OutgoingTurn {
  requestId: string
  conversationId: string | null
  content: string
  stage: 'submitting' | 'preparing' | 'replying' | 'uncertain' | 'failed' | 'cancelled'
  runId: string | null
  error: string
}
const outgoing = ref<OutgoingTurn | null>(null)
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
  activeRun.value?.id === outgoing.value?.runId ? activeRun.value : null
)
const preparationLabel = computed(() => {
  if (preparation.value?.status === 'cancel_requested') return '正在停止检索…'
  if (preparation.value?.phase.includes('rerank')) return '正在整理相关资料…'
  return '正在检索知识库…'
})
const history = ref<HTMLElement | null>(null)
const pageScope = new RequestScope(),
  conversationScope = new RequestScope()
let identityController: AbortController | null = null
const published = computed(() => agent.value?.publishedConfig ?? null)
const name = computed(
  () => selected.value?.name ?? published.value?.name ?? agent.value?.name ?? '智能助手'
)
const welcome = computed(
  () => selected.value?.welcomeMessage ?? published.value?.welcomeMessage ?? ''
)
const running = computed(() =>
  Boolean(
    (activeRun.value && !isTerminal(activeRun.value)) ||
      (preparation.value && !preparationFinished(preparation.value))
  )
)
const canSend = computed(
  () =>
    canUseCapability('agents') &&
    agent.value?.status === 'published' &&
    !recoveryBlocked.value &&
    (!route.query.conversation || selected.value?.id === route.query.conversation) &&
    !loading.value &&
    !sending.value &&
    !running.value
)
interface PendingSend {
  owner: string
  agent: string
  conversationId: string | null
  createId: string
  requestId: string
  message: string
}
const pending = ref<PendingSend | null>(null)
const recoveryBlocked = ref(false)
const pendingKey = () => `zhiheng:assistant:${identity.value?.id ?? ''}:${agentId}:pending`
function showOutgoing(
  requestId: string,
  content: string,
  conversationId: string | null,
  stage: OutgoingTurn['stage']
): void {
  outgoing.value = { requestId, content, conversationId, stage, runId: null, error: '' }
}
function savePending(value: PendingSend): void {
  sessionStorage.setItem(pendingKey(), JSON.stringify(value))
  pending.value = value
}
function clearPending(): void {
  sessionStorage.removeItem(pendingKey())
  pending.value = null
}
function restorePending(): void {
  try {
    const raw = sessionStorage.getItem(pendingKey())
    if (!raw) return
    const d: unknown = JSON.parse(raw)
    if (typeof d !== 'object' || d === null) throw new Error('invalid')
    const v = d as Record<string, unknown>
    if (
      typeof v.owner !== 'string' ||
      v.owner !== identity.value?.id ||
      v.agent !== agentId ||
      typeof v.createId !== 'string' ||
      typeof v.requestId !== 'string' ||
      typeof v.message !== 'string' ||
      !(v.conversationId === null || typeof v.conversationId === 'string')
    )
      throw new Error('invalid')
    pending.value = {
      owner: v.owner,
      agent: agentId,
      createId: v.createId,
      requestId: v.requestId,
      message: v.message,
      conversationId: v.conversationId
    }
  } catch {
    recoveryBlocked.value = true
    error.value = '无法读取原提交，请先核对历史对话及浏览器会话存储，再刷新重试。'
  }
}
function restorePendingOutgoing(): void {
  const value = pending.value
  if (value && !value.conversationId && !route.query.conversation) {
    showOutgoing(value.requestId, value.message, null, 'uncertain')
  }
}
async function scroll(): Promise<void> {
  await nextTick()
  history.value?.scrollTo({ top: history.value.scrollHeight })
}
function project(run: RunSnapshot): void {
  if (acceptSnapshot(activeRun.value, run)) activeRun.value = run
  turns.value = turns.value.map((turn) =>
    turn.run.id === run.id && acceptSnapshot(turn.run, run) ? { ...turn, run } : turn
  )
  void scroll()
}
async function refreshList(append = false): Promise<void> {
  const ticket = pageScope.replace()
  listError.value = ''
  listLoading.value = true
  try {
    const cursor = append ? conversations.value.at(-1)?.id : undefined
    const rows = await getAssistantConversations(agentId, ticket.signal, cursor)
    if (!ticket.isCurrent()) return
    conversations.value = append ? [...conversations.value, ...rows] : rows
    hasMore.value = rows.length === 50
  } catch (e) {
    if (ticket.isCurrent() && !isAborted(e)) listError.value = errorMessage(e)
  } finally {
    if (ticket.isCurrent()) listLoading.value = false
  }
}
async function readDetail(
  id: string,
  ticket: ReturnType<RequestScope['current']>
): Promise<AssistantConversation | undefined> {
  const result = await getAssistantConversation(agentId, id, ticket.signal)
  if (!ticket.isCurrent()) return
  selected.value = result.conversation
  turns.value = result.turns
  older.value = result.nextBefore
  const outstanding = [...result.turns].reverse().find((t) => !isTerminal(t.run))
  activeRun.value = outstanding?.run ?? null
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
      'preparing'
    )
  } else if (
    !outgoing.value &&
    pending.value?.conversationId === id &&
    !result.turns.some((turn) => turn.requestId === pending.value?.requestId)
  ) {
    showOutgoing(pending.value.requestId, pending.value.message, id, 'uncertain')
  }
  return result.conversation
}
async function follow(
  run: RunSnapshot,
  ticket: ReturnType<RequestScope['current']>
): Promise<void> {
  if (run.resource_type !== 'agent_chat' || run.resource_id !== selected.value?.id)
    throw new Error('助手运行与当前对话不匹配')
  activeRun.value = run
  project(run)
  await observeRun(
    run,
    (value) => {
      if (ticket.isCurrent()) project(value)
    },
    ticket.signal
  )
  if (ticket.isCurrent() && selected.value) {
    await readDetail(selected.value.id, ticket)
    void refreshList()
    await scroll()
  }
}
async function followKnowledge(
  value: KnowledgePreparation,
  ticket: ReturnType<RequestScope['current']>
): Promise<void> {
  if (value.resource_type !== 'agent_chat' || value.resource_id !== selected.value?.id)
    throw new Error('知识准备与当前助手对话不匹配')
  preparation.value = value
  const result = await observePreparation(
    value,
    (incoming) => {
      if (ticket.isCurrent()) preparation.value = incoming
    },
    ticket.signal
  )
  if (!ticket.isCurrent()) return
  if (!result.run) {
    if (outgoing.value) {
      outgoing.value.stage = result.status === 'cancelled' ? 'cancelled' : 'failed'
      outgoing.value.error =
        result.status === 'cancelled' ? '' : (result.error?.message ?? '知识准备未完成')
    } else error.value = result.error?.message ?? '知识准备未完成'
    void refreshList()
    await scroll()
    return
  }
  preparation.value = null
  activeRun.value = result.run
  if (outgoing.value) {
    outgoing.value.stage = 'replying'
    outgoing.value.runId = result.run.id
  }
  await readDetail(selected.value.id, ticket)
  if (ticket.isCurrent()) await follow(result.run, ticket)
}
async function openConversation(id: string): Promise<void> {
  const ticket = conversationScope.replace()
  turns.value = []
  selected.value = null
  activeRun.value = null
  preparation.value = null
  outgoing.value = null
  older.value = null
  olderLoading.value = false
  draft.value = ''
  error.value = ''
  disconnected.value = false
  stopping.value = false
  loading.value = Boolean(id)
  sending.value = false
  if (!id) return
  try {
    const loaded = await readDetail(id, ticket)
    if (!ticket.isCurrent()) return
    await scroll()
    if (loaded?.preparationId) {
      const value = await getPreparation(loaded.preparationId, ticket.signal)
      if (ticket.isCurrent()) {
        loading.value = false
        await followKnowledge(value, ticket)
      }
    } else {
      loading.value = false
      if (activeRun.value) await follow(activeRun.value, ticket)
    }
  } catch (e) {
    if (ticket.isCurrent() && !isAborted(e)) {
      error.value = errorMessage(e)
      disconnected.value = running.value
    }
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function select(id?: string): void {
  void router.replace({
    name: 'assistant-chat',
    params: { id: agentId },
    query: id ? { conversation: id } : {}
  })
}
async function send(retry = false): Promise<void> {
  if (sending.value || loading.value || running.value || !identity.value) return
  if (!retry && (!canSend.value || pending.value || !draft.value.trim())) return
  const intent = retry
    ? pending.value
    : {
        owner: identity.value.id,
        agent: agentId,
        conversationId: selected.value?.id ?? null,
        createId: createRequestId(),
        requestId: createRequestId(),
        message: draft.value.trim()
      }
  if (!intent) return
  // Persist the original identity before any mutation, so an uncertain response can be retried safely.
  try {
    savePending(intent)
  } catch {
    error.value = '无法保存提交恢复信息，请检查浏览器存储后重试'
    return
  }
  if (intent.conversationId && intent.conversationId !== selected.value?.id) {
    await router.replace({
      name: 'assistant-chat',
      params: { id: agentId },
      query: { conversation: intent.conversationId }
    })
    await openConversation(intent.conversationId)
    if (running.value) return
  }
  const ticket = conversationScope.current()
  sending.value = true
  error.value = ''
  disconnected.value = false
  showOutgoing(intent.requestId, intent.message, intent.conversationId, 'submitting')
  draft.value = ''
  void scroll()
  try {
    if (!intent.conversationId) {
      const value = await createAssistantConversation(agentId, intent.createId, ticket.signal)
      if (!ticket.isCurrent()) return
      selected.value = value
      intent.conversationId = value.id
      if (outgoing.value) outgoing.value.conversationId = value.id
      savePending(intent)
      // Replace the URL without reloading the just-created session before its first submission.
      skipConversation = value.id
      await router.replace({
        name: 'assistant-chat',
        params: { id: agentId },
        query: { conversation: value.id }
      })
    }
    const result = await sendAssistantMessage(
      agentId,
      intent.conversationId,
      intent.message,
      intent.requestId,
      ticket.signal
    )
    if (!ticket.isCurrent()) return
    clearPending()
    sending.value = false
    void refreshList()
    if (isPreparation(result)) {
      if (outgoing.value) outgoing.value.stage = 'preparing'
      await followKnowledge(result, ticket)
    } else {
      activeRun.value = result
      if (outgoing.value) {
        outgoing.value.stage = 'replying'
        outgoing.value.runId = result.id
      }
      await readDetail(intent.conversationId, ticket)
      if (ticket.isCurrent()) await follow(result, ticket)
    }
  } catch (e) {
    if (ticket.isCurrent() && !isAborted(e)) {
      const definitive = e instanceof ApiError && e.status < 500 && e.status !== 408
      if (definitive) clearPending()
      if (outgoing.value) {
        outgoing.value.error = errorMessage(e)
        outgoing.value.stage = pending.value ? 'uncertain' : 'failed'
      } else error.value = errorMessage(e)
      disconnected.value = running.value
    }
  } finally {
    if (ticket.isCurrent()) sending.value = false
  }
}
async function recover(): Promise<void> {
  if (selected.value) await openConversation(selected.value.id)
}
async function stop(): Promise<void> {
  if (stopping.value) return
  const ticket = conversationScope.current()
  stopping.value = true
  try {
    if (preparation.value && !preparationFinished(preparation.value)) {
      const value = await cancelPreparation(preparation.value.id, ticket.signal)
      if (ticket.isCurrent()) preparation.value = value
    } else if (activeRun.value) {
      const value = await cancelRun(activeRun.value.id, ticket.signal)
      if (ticket.isCurrent()) project(value)
    }
  } catch (e) {
    if (ticket.isCurrent() && !isAborted(e)) error.value = errorMessage(e)
  } finally {
    if (ticket.isCurrent()) stopping.value = false
  }
}
async function loadOlder(): Promise<void> {
  if (!selected.value || older.value === null || olderLoading.value) return
  const ticket = conversationScope.current()
  olderLoading.value = true
  try {
    const value = await getAssistantConversation(
      agentId,
      selected.value.id,
      ticket.signal,
      older.value
    )
    if (ticket.isCurrent()) {
      turns.value = [...value.turns, ...turns.value]
      older.value = value.nextBefore
    }
  } catch (e) {
    if (ticket.isCurrent() && !isAborted(e)) error.value = errorMessage(e)
  } finally {
    if (ticket.isCurrent()) olderLoading.value = false
  }
}
function renamed(value: AssistantConversation): void {
  pageScope.replace()
  listLoading.value = false
  conversations.value = conversations.value.map((item) => (item.id === value.id ? value : item))
  if (selected.value?.id === value.id) selected.value = value
}
function removed(id: string): void {
  pageScope.replace()
  listLoading.value = false
  conversations.value = conversations.value.filter((item) => item.id !== id)
  if (selected.value?.id === id || route.query.conversation === id) {
    conversationScope.replace()
    turns.value = []
    selected.value = null
    activeRun.value = null
    preparation.value = null
    outgoing.value = null
    select()
  }
}
let skipConversation = ''
watch(
  () => route.query.conversation,
  (value) => {
    const id = typeof value === 'string' ? value : ''
    if (id && id === skipConversation) {
      skipConversation = ''
      return
    }
    void openConversation(id)
  },
  { immediate: true }
)
watch(
  () => identity.value?.id,
  async (owner) => {
    pageScope.replace()
    conversationScope.replace()
    historyOpen.value = false
    agent.value = null
    selected.value = null
    turns.value = []
    conversations.value = []
    activeRun.value = null
    preparation.value = null
    outgoing.value = null
    pending.value = null
    recoveryBlocked.value = false
    draft.value = ''
    error.value = ''
    listError.value = ''
    listLoading.value = false
    loading.value = false
    sending.value = false
    older.value = null
    identityController?.abort()
    if (!owner) return
    restorePending()
    restorePendingOutgoing()
    const controller = new AbortController()
    identityController = controller
    try {
      const value = await getAgent(agentId, controller.signal)
      if (!controller.signal.aborted) agent.value = value
    } catch (e) {
      if (!controller.signal.aborted && !isAborted(e)) error.value = errorMessage(e)
    }
    if (!controller.signal.aborted) {
      void refreshList()
      const id = route.query.conversation
      if (typeof id === 'string') void openConversation(id)
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  pageScope.dispose()
  conversationScope.dispose()
  identityController?.abort()
  draft.value = ''
})
</script>

<template>
  <section class="assistant-chat workspace-chat-page">
    <AssistantHistoryDialog
      v-if="identity"
      :key="identity.id"
      v-model:open="historyOpen"
      :agent-id="agentId"
      :conversations="conversations"
      :selected-id="selected?.id"
      :locked-id="running || sending ? selected?.id : undefined"
      :pending-id="pending?.conversationId ?? undefined"
      :loading="listLoading"
      :error="listError"
      :more="hasMore"
      @select="select"
      @refresh="refreshList()"
      @more="refreshList(true)"
      @changed="renamed"
      @removed="removed"
    />
    <header class="chat-toolbar">
      <AButton
        type="text"
        class="chat-toolbar-action"
        aria-label="返回智能助手"
        title="返回智能助手"
        @click="router.push('/agents')"
        ><ArrowLeftOutlined
      /></AButton>
      <h1 class="assistant-chat__name" :title="name">{{ name }}</h1>
      <button
        type="button"
        class="chat-toolbar-action"
        aria-label="历史记录"
        title="历史记录"
        aria-haspopup="dialog"
        aria-controls="assistant-history-dialog"
        :aria-expanded="historyOpen"
        @click="historyOpen = true"
      >
        <HistoryOutlined aria-hidden="true" /><span class="chat-toolbar__label">历史记录</span>
      </button>
      <AButton
        type="text"
        class="chat-toolbar-action"
        aria-label="新建对话"
        title="新建对话"
        @click="select()"
        ><PlusOutlined aria-hidden="true" /><span class="chat-toolbar__label"
          >新建对话</span
        ></AButton
      >
    </header>
    <div v-if="selected" class="chat-session-heading" :title="selected.title">
      {{ selected.title }}
    </div>
    <section ref="history" class="message-history" aria-label="助手消息" :aria-busy="loading">
      <ASpin v-if="loading" class="page-spinner" />
      <div v-if="older !== null" class="message-history__earlier">
        <AButton type="text" :loading="olderLoading" @click="loadOlder">加载更早消息</AButton>
      </div>
      <article v-if="!loading && agent" class="message-row assistant assistant-chat__welcome">
        <div class="message-body">
          <AssistantWelcome
            :text="welcome || published?.description || '你好，有什么可以帮你？'"
            :animate="!selected && !turns.length && !outgoing"
          />
        </div>
      </article>
      <template v-for="turn in turns" :key="turn.id">
        <article class="message-row user assistant-chat__question">
          <div class="message-body">
            <p class="user-message">{{ turn.content }}</p>
          </div>
        </article>
        <article class="message-row assistant assistant-chat__reply">
          <div class="message-body">
            <AiMessage
              :content="turn.run.text"
              :reasoning-content="turn.run.reasoning ?? ''"
              :loading="!isTerminal(turn.run)"
            />
            <KnowledgeSources :sources="turn.sources" />
            <small
              v-if="turn.run.status !== 'completed'"
              class="message-run-status"
              :class="{ 'is-failed': !!turn.run.error }"
              >{{ runStatusLabel[turn.run.status]
              }}<template v-if="turn.run.error"> · {{ turn.run.error.message }}</template></small
            >
          </div>
        </article>
      </template>
      <template v-if="outgoingVisible && outgoing">
        <article class="message-row user assistant-chat__question">
          <div class="message-body">
            <p class="user-message">{{ outgoing.content }}</p>
          </div>
        </article>
        <article class="message-row assistant assistant-chat__reply assistant-chat__pending">
          <div class="message-body">
            <AiMessage
              v-if="outgoingRun"
              :content="outgoingRun.text"
              :reasoning-content="outgoingRun.reasoning ?? ''"
              :loading="!isTerminal(outgoingRun)"
            />
            <div
              v-else-if="sending || (preparation && !preparationFinished(preparation))"
              class="assistant-progress"
              role="status"
            >
              <ASpin size="small" /><span>{{ sending ? '正在提交…' : preparationLabel }}</span>
            </div>
            <p v-else-if="outgoing.stage === 'cancelled'" class="message-run-status">已停止检索</p>
            <p v-else-if="outgoing.stage === 'uncertain'" class="message-run-status">
              提交结果待确认
            </p>
            <p v-if="outgoing.error" class="message-run-status is-failed" role="alert">
              {{ outgoing.error }}
            </p>
            <AButton
              v-if="pending"
              size="small"
              :disabled="sending || loading || running"
              @click="send(true)"
              >确认原提交</AButton
            >
          </div>
        </article>
      </template>
      <article
        v-if="error || (pending && !outgoingVisible && !sending) || disconnected"
        class="message-row assistant assistant-chat__notice"
      >
        <div class="message-body">
          <AAlert v-if="error" type="error" :message="error" show-icon />
          <AAlert
            v-if="pending && !outgoingVisible && !sending"
            type="warning"
            message="有一条原提交待确认，请确认原提交以避免重复调用。"
            show-icon
            ><template #action
              ><AButton :disabled="sending || loading || running" @click="send(true)"
                >确认原提交</AButton
              ></template
            ></AAlert
          >
          <AButton v-if="disconnected" @click="recover">恢复查看</AButton>
        </div>
      </article>
    </section>
    <footer class="composer">
      <div v-if="agent?.status === 'draft'" class="composer-notice">
        <AAlert
          v-if="agent?.status === 'draft'"
          type="info"
          message="助手尚未发布，发布后可开始正式对话。草稿可在配置页调试。"
          show-icon
        />
      </div>
      <div class="composer-surface">
        <div class="composer-input-area">
          <ATextarea
            v-model:value="draft"
            aria-label="发送给助手的消息"
            :placeholder="`向${name}发送消息`"
            :maxlength="16000"
            :auto-size="{ minRows: 2, maxRows: 7 }"
            :disabled="!canSend || !!pending"
            @keydown.enter.exact.prevent="!$event.isComposing && send()"
          />
        </div>
        <div class="composer-actions">
          <AButton
            v-if="running"
            class="composer-submit"
            :loading="stopping"
            aria-label="停止生成"
            title="停止生成"
            @click="stop"
            ><BorderOutlined
          /></AButton>
          <AButton
            v-else
            class="composer-submit"
            type="primary"
            aria-label="发送"
            title="发送消息（Enter），Shift + Enter 换行"
            :disabled="!canSend || !draft.trim() || !!pending"
            :loading="sending"
            @click="send()"
            ><ArrowUpOutlined
          /></AButton>
        </div>
      </div>
    </footer>
  </section>
</template>

<style scoped>
.assistant-chat {
  --workspace-font-size-chat: 17px;
  --workspace-line-height-chat: 1.8;
  --chat-column-width: 920px;
  --chat-gutter: 24px;
  --chat-composer-padding: 20px;
  --chat-inset: calc(var(--chat-gutter) + var(--chat-composer-padding) + 1px);
  height: 100%;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}
.chat-toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  min-height: 70px;
  padding: 12px 24px;
}
.assistant-chat__name {
  min-width: 0;
  flex: 1;
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 16px;
  font-weight: 500;
}
button.chat-toolbar-action {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: var(--workspace-control-height);
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}
button.chat-toolbar-action:hover {
  background: var(--gray-50);
}
button.chat-toolbar-action:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.chat-session-heading {
  flex-shrink: 0;
  padding: 0 28px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.message-history {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px max(var(--chat-inset), calc((100% - var(--chat-column-width)) / 2));
  background: transparent;
}
.message-history__earlier {
  text-align: center;
  margin-bottom: 12px;
}
.message-row {
  align-items: flex-start;
  display: flex;
  max-width: var(--chat-column-width);
  margin: 0 auto 30px;
}
.message-row.user {
  justify-content: flex-end;
}
.message-body {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.message-row.assistant .message-body {
  flex: 1;
}
.message-row.user .message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: 85%;
  text-align: left;
}
.user-message {
  margin: 0;
  padding: 10px 18px;
  max-width: 100%;
  border: 0;
  border-radius: 18px;
  background: #262626;
  color: #fff;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
}
.message-run-status {
  display: block;
  margin-top: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.message-run-status.is-failed {
  color: var(--workspace-danger);
}
.assistant-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-helper);
  line-height: var(--workspace-line-height);
}
.assistant-chat__pending :deep(.ant-btn),
.assistant-chat__notice :deep(.ant-btn) {
  margin-top: 8px;
}
.assistant-chat__notice .message-body {
  display: grid;
  gap: 8px;
}
.composer {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  min-height: 0;
  max-height: calc(100% - 180px);
  width: min(calc(var(--chat-column-width) + var(--chat-inset) * 2), 100%);
  align-self: center;
  padding: 10px var(--chat-gutter) 12px;
  background: transparent;
}
.composer-notice {
  display: grid;
  flex-shrink: 0;
  gap: 8px;
  margin-bottom: 10px;
  overflow: auto;
}
.composer-notice :deep(.ant-alert) {
  border-radius: 12px;
}
.composer-surface {
  display: flex;
  min-height: 0;
  flex: 0 1 auto;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 24px;
  background: var(--bg-container);
  transition:
    border-color 0.18s,
    box-shadow 0.18s;
}
.composer-surface:focus-within {
  border-color: var(--main-400);
  box-shadow:
    0 0 0 1px var(--main-50),
    0 4px 20px rgb(0 0 0 / 3%);
}
.composer-input-area {
  min-height: 0;
  flex: 0 1 auto;
  overflow-y: auto;
  padding: 16px var(--chat-composer-padding) 0;
}
.composer-surface :deep(.ant-input) {
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}
.composer :deep(textarea) {
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
}
.composer-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px var(--chat-composer-padding) 12px;
}
.composer-submit {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  padding: 0;
  border-radius: 50%;
  font-size: 19px;
}
.composer-submit:not(:disabled) {
  border-color: #262626;
  background: #262626;
  color: #fff;
}
.composer-submit:not(:disabled):hover {
  border-color: #434343;
  background: #434343;
}
@media (max-width: 760px) {
  .assistant-chat {
    --chat-gutter: 14px;
    --chat-composer-padding: 16px;
  }
  .chat-toolbar {
    padding: 12px 14px;
  }
  .chat-session-heading {
    padding: 0 18px;
  }
}
@media (max-width: 600px) {
  .chat-toolbar__label {
    display: none;
  }
  button.chat-toolbar-action {
    width: var(--workspace-control-height);
    padding: 0;
  }
}
</style>
