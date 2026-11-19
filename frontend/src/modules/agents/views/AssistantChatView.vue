<script setup lang="ts">
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
import { useRouter } from 'vue-router'
import { AiMessage } from '../../chat/public.ts'
import { CHAT_FILE_ACCEPT } from '../../../shared/attachmentQueue.ts'
import AttachmentButton from '../../../shared/components/AttachmentButton.vue'
import AttachmentCard from '../../../shared/components/AttachmentCard.vue'
import { computed, ref } from 'vue'
import { formatFileSize } from '../../files/public.ts'
import AssistantMessageFiles from '../components/AssistantMessageFiles.vue'
import { preparationFinished } from '../../knowledge/public.ts'
import { identity } from '../../identity/public.ts'
import { isTerminal, runStatusLabel } from '../../../shared/stream.ts'
import AssistantWelcome from '../components/AssistantWelcome.vue'
import AssistantHistoryDialog from '../components/AssistantHistoryDialog.vue'
import { useAssistantWorkspace } from '../composables/useAssistantWorkspace.ts'

const router = useRouter()
const {
  agentId,
  queuedFiles,
  fileQueueReady,
  addFiles,
  removeFile,
  retryFile,
  agent,
  conversations,
  selected,
  turns,
  older,
  hasMore,
  historyOpen,
  listLoading,
  olderLoading,
  error,
  listError,
  loading,
  history,
  configuration,
  name,
  welcome,
  canSend,
  outgoing,
  draft,
  sending,
  pending,
  outgoingVisible,
  outgoingRun,
  preparation,
  disconnected,
  stopping,
  stopAcknowledged,
  running,
  preparationLabel,
  send,
  stop,
  select,
  refreshList,
  loadOlder,
  recover,
  renamed,
  removed
} = useAssistantWorkspace()
const fileInput = ref<HTMLInputElement | null>(null)
const queuedFileBytes = computed(() =>
  queuedFiles.value.reduce((total, file) => total + file.sizeBytes, 0)
)
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files) addFiles(input.files)
  input.value = ''
}
function pasteFiles(event: ClipboardEvent): void {
  if (event.clipboardData?.files.length) {
    event.preventDefault()
    addFiles(event.clipboardData.files)
  }
}
function dropFiles(event: DragEvent): void {
  if (event.dataTransfer?.files.length) addFiles(event.dataTransfer.files)
}
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
            :text="welcome || configuration?.description || '你好，有什么可以帮你？'"
            :animate="!selected && !turns.length && !outgoing"
          />
        </div>
      </article>
      <template v-for="turn in turns" :key="turn.id">
        <article class="message-row user assistant-chat__question">
          <div class="message-body">
            <AssistantMessageFiles
              :agent-id="agentId"
              :conversation-id="selected?.id ?? null"
              :files="turn.attachments"
            />
            <p class="user-message">{{ turn.content }}</p>
          </div>
        </article>
        <article class="message-row assistant assistant-chat__reply">
          <div class="message-body">
            <AiMessage
              :content="turn.run.text"
              :sources="turn.sources"
              :reasoning-content="turn.run.reasoning ?? ''"
              :loading="!isTerminal(turn.run)"
            />
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
            <AssistantMessageFiles
              :agent-id="agentId"
              :conversation-id="outgoing.conversationId"
              :files="outgoing.attachments"
              disabled
            />
            <p class="user-message">{{ outgoing.content }}</p>
          </div>
        </article>
        <article class="message-row assistant assistant-chat__reply assistant-chat__pending">
          <div class="message-body">
            <AiMessage
              v-if="outgoingRun"
              :content="outgoingRun.text"
              :sources="preparation?.hits ?? []"
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
    <footer class="composer" @dragover.prevent @drop.prevent="dropFiles">
      <input
        ref="fileInput"
        type="file"
        multiple
        :accept="CHAT_FILE_ACCEPT"
        aria-label="选择助手附件"
        class="attachment-input"
        @change="chooseFiles"
      />
      <div v-if="agent?.status === 'disabled'" class="composer-notice">
        <AAlert
          v-if="agent?.status === 'disabled'"
          type="info"
          message="助手尚未启用，启用后可开始正式对话。"
          show-icon
        />
      </div>
      <div class="composer-surface">
        <div
          v-if="queuedFiles.length"
          class="assistant-file-queue"
          role="region"
          aria-label="待发送附件"
          tabindex="0"
        >
          <AttachmentCard
            v-for="file in queuedFiles"
            :key="file.id"
            :filename="file.fileName"
            :size-label="formatFileSize(file.sizeBytes)"
            :mime="file.kind === 'image' ? 'image/*' : ''"
            :preview-url="file.previewUrl"
            :status="file.status"
            :error="file.error"
            :disabled="!canSend || !!pending"
            @retry="retryFile(file.id)"
            @remove="removeFile(file.id)"
          />
        </div>
        <p v-if="queuedFiles.length" class="file-queue-usage" aria-label="附件总大小">
          {{ formatFileSize(queuedFileBytes) }} / 50 MB
        </p>
        <div
          class="composer-input-area"
          :class="{ 'has-files': queuedFiles.length }"
          @paste="pasteFiles"
        >
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
          <AttachmentButton
            class="attachment-button"
            aria-label="添加助手附件"
            title="每条消息附件合计最多50MB，单个文件最大20MiB；图片需要模型支持视觉"
            :disabled="!canSend || !!pending"
            @click="fileInput?.click()"
          />
          <AButton
            v-if="running"
            class="composer-submit"
            :loading="stopping"
            :disabled="stopAcknowledged"
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
            :disabled="
              !canSend || (!draft.trim() && !queuedFiles.length) || !fileQueueReady || !!pending
            "
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
.attachment-input {
  display: none;
}
.attachment-button {
  margin-right: auto;
}
.assistant-file-queue {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  scrollbar-width: none;
  flex-shrink: 0;
  padding: 18px calc(var(--chat-composer-padding) + 2px) 2px;
}
.assistant-file-queue::-webkit-scrollbar {
  display: none;
}
.file-queue-usage {
  margin: 6px calc(var(--chat-composer-padding) + 2px) 12px;
  color: var(--color-text-tertiary);
  font-size: var(--workspace-font-size-caption);
}
.composer-input-area.has-files {
  padding-top: 0;
}

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
