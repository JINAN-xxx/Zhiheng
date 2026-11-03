<!-- Adapted from the original chat/index.vue, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Avatar as AAvatar,
  Button as AButton,
  Select as ASelect,
  Spin as ASpin,
  Tag as ATag,
  Textarea as ATextarea
} from 'ant-design-vue'
import {
  HistoryOutlined,
  PlusOutlined,
  PaperClipOutlined,
  CloseOutlined,
  FileTextOutlined,
  PictureOutlined,
  PauseCircleOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  UserOutlined
} from '@ant-design/icons-vue'
import { runStatusLabel } from '../../../shared/stream.ts'
import { useChatWorkspace } from '../public.ts'
import ChatHistoryDialog from '../components/ChatHistoryDialog.vue'
import ChatGenerationSettingsDialog from '../components/ChatGenerationSettingsDialog.vue'
import MessageActions from '../../../shared/components/MessageActions.vue'
import { SkillPicker } from '../../skills/public.ts'
import { KnowledgeSources } from '../../knowledge/public.ts'
import AiMessage from '../components/AiMessage.vue'
import ChatEmptyState from '../components/ChatEmptyState.vue'
import ChatMessageFiles from '../components/ChatMessageFiles.vue'
import MessageShareDialog from '../components/MessageShareDialog.vue'
import { groupMessageVersions } from '../composables/messageVersions.ts'
import type { ChatMessage } from '../api/index.ts'
import { CHAT_FILE_ACCEPT } from '../attachments.ts'
const {
  selectConversation,
  selected,
  queuedFiles,
  addFiles,
  removeFile,
  retryFile,
  fileQueueReady,
  readOnly,
  modelId,
  messages,
  draft,
  selectedSkills,
  activeRunSources,
  preparing,
  skillPickerOpen,
  removeSkill,
  handleSkillSelected,
  error,
  listError,
  loading,
  listLoading,
  historyReady,
  sending,
  stopping,
  stopAcknowledged,
  activeRun,
  messagesElement,
  running,
  canRecover,
  modelOptions,
  selectedModel,
  generationSettings,
  applyGenerationSettings,
  regenerate,
  retryRegeneration,
  regenerationPending,
  canRetryRegeneration,
  regenerationReason,
  messageActionReason,
  shareReason,
  feedback,
  feedbackBusy,
  canRetry,
  send,
  stop,
  reconnect,
  setModelId,
  keydown
} = useChatWorkspace()
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
const historyOpen = ref(false)
const settingsOpen = ref(false)
async function newConversation(): Promise<void> {
  await selectConversation(null)
}
const fileInput = ref<HTMLInputElement | null>(null)
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files) addFiles(Array.from(input.files))
  input.value = ''
}
onBeforeUnmount(() => {
  void selectConversation(null)
})
</script>
<template>
  <div
    class="platform-chat workspace-chat-page"
    :class="{ 'is-empty': !messages.length && !activeRun && !selected }"
  >
    <ChatHistoryDialog v-model:open="historyOpen" />
    <ChatGenerationSettingsDialog
      v-model:open="settingsOpen"
      :settings="generationSettings"
      :model="selectedModel"
      :disabled="running || sending || loading || listLoading || readOnly || preparing"
      @save="applyGenerationSettings"
    />
    <main class="chat-workspace">
      <header class="chat-toolbar">
        <button
          type="button"
          class="chat-toolbar-action"
          aria-label="历史记录"
          aria-haspopup="dialog"
          aria-controls="chat-history-dialog"
          :aria-expanded="historyOpen"
          title="历史记录"
          @click="historyOpen = true"
        >
          <HistoryOutlined aria-hidden="true" /><span class="chat-toolbar__label">历史记录</span>
        </button>
        <AButton
          type="text"
          class="chat-toolbar-action"
          aria-label="新建对话"
          title="新建对话"
          @click="newConversation"
          ><PlusOutlined aria-hidden="true" /><span class="chat-toolbar__label">新建对话</span>
        </AButton>
        <button
          class="toolbar-icon-button"
          type="button"
          aria-label="对话设置"
          aria-haspopup="dialog"
          :aria-expanded="settingsOpen"
          title="对话设置"
          @click="settingsOpen = true"
        >
          <SettingOutlined />
        </button>
      </header>
      <div v-if="selected" class="chat-session-heading">{{ selected.title }}</div>
      <div
        v-if="error || listError || canRetry || canRecover || canRetryRegeneration"
        class="chat-alert"
      >
        <AAlert v-if="error" :message="error" type="error" show-icon /><AAlert
          v-if="listError"
          :message="listError"
          type="error"
          show-icon
        />
        <AButton v-if="canRetry" size="small" @click="send(true)">使用原请求重试</AButton
        ><AButton v-if="canRetryRegeneration" size="small" @click="retryRegeneration"
          >使用原重新生成请求重试</AButton
        ><AButton v-if="canRecover" size="small" @click="reconnect">恢复查看</AButton>
      </div>
      <section
        ref="messagesElement"
        class="message-history"
        aria-live="polite"
        aria-label="聊天消息"
      >
        <ASpin v-if="loading" class="page-spinner" />
        <ChatEmptyState
          v-else-if="!messages.length && !activeRun && !selected"
          @prompt="draft = $event"
        />
        <article
          v-for="row in messageRows"
          :id="`message-${row.message.id}`"
          :key="row.key"
          class="message-row"
          :class="row.message.role"
        >
          <AAvatar class="message-avatar" :size="30"
            ><UserOutlined v-if="row.message.role === 'user'" /><RobotOutlined v-else
          /></AAvatar>
          <div class="message-body">
            <div class="message-meta">
              {{ row.message.role === 'user' ? '我' : '智衡' }}
              <span
                v-if="isActive(row.message) && activeRun && activeRun.status !== 'completed'"
                class="message-run-status"
                :class="`is-${activeRun.status}`"
                >{{ replyStatus }}</span
              >
            </div>
            <AiMessage
              v-if="row.message.role === 'assistant'"
              :content="messageText(row.message)"
              :reasoning-content="
                isActive(row.message) ? (activeRun?.reasoning ?? '') : row.message.reasoning
              "
              :loading="isActive(row.message) && running"
            />
            <p v-else class="user-message">{{ row.message.content }}</p>
            <p v-if="isActive(row.message) && activeRun?.error" class="run-error">
              {{ activeRun.error.message }}
            </p>
            <ChatMessageFiles :files="row.message.attachments" />
            <KnowledgeSources :sources="row.message.knowledge_sources" />
            <div v-if="row.message.skill_snapshots.length" class="message-skills">
              <ATag v-for="skill in row.message.skill_snapshots" :key="skill.revision_id"
                >技能：{{ skill.name }}</ATag
              >
            </div>
            <span
              v-if="isActive(row.message) && activeRun?.usage.status === 'unknown'"
              class="muted small"
              >用量待核对</span
            >
            <div v-if="row.versions.length > 1" class="reply-versions" aria-label="回复版本">
              <button
                type="button"
                aria-label="上一个回复版本"
                :disabled="row.index === 0"
                @click="selectedVersions[row.key] = row.versions[row.index - 1]!.id"
              >
                ‹
              </button>
              <span>{{ row.index + 1 }} / {{ row.versions.length }}</span>
              <button
                type="button"
                aria-label="下一个回复版本"
                :disabled="row.index === row.versions.length - 1"
                @click="selectedVersions[row.key] = row.versions[row.index + 1]!.id"
              >
                ›
              </button>
            </div>
            <MessageActions
              :key="row.message.id"
              :content="messageText(row.message)"
              :role="row.message.role"
              :regenerate="row.message.role === 'assistant'"
              :feedback-enabled="row.message.role === 'assistant'"
              :share="row.message.role === 'assistant'"
              :feedback-value="row.message.feedback"
              :feedback-busy="feedbackBusy.has(row.message.id)"
              :regenerate-reason="regenerationReason(row.message)"
              :feedback-reason="messageActionReason(row.message)"
              :share-reason="shareReason(row.message)"
              @regenerate="regenerateReply(row.message, row.key)"
              @feedback="feedback(row.message, $event)"
              @share="shareMessageId = row.message.id"
            />
          </div>
        </article>
        <article v-if="activeRun && !activeRunRepresented" class="message-row assistant">
          <AAvatar class="message-avatar" :size="30"><RobotOutlined /></AAvatar>
          <div class="message-body">
            <div class="message-meta">
              智衡
              <span
                v-if="activeRun.status !== 'completed'"
                class="message-run-status"
                :class="`is-${activeRun.status}`"
                >{{ replyStatus }}</span
              >
            </div>
            <AiMessage
              :content="activeRun.text"
              :reasoning-content="activeRun.reasoning ?? ''"
              :loading="running"
            />
            <p v-if="activeRun.error" class="run-error">{{ activeRun.error.message }}</p>
            <KnowledgeSources :sources="activeRunSources" />
            <span v-if="activeRun.usage.status === 'unknown'" class="muted small">用量待核对</span>
            <p v-if="!running && !activeRun.text && !activeRun.error" class="empty-reply">
              {{ activeRun.status === 'cancelled' ? '已停止生成' : '本次回复未生成内容' }}
            </p>
            <MessageActions
              v-if="activeRun.text || unansweredQuestion"
              :content="activeRun.text"
              :regenerate="Boolean(unansweredQuestion)"
              :regenerate-reason="unansweredQuestion ? regenerationReason(unansweredQuestion) : ''"
              @regenerate="
                unansweredQuestion &&
                regenerateReply(unansweredQuestion, `reply:${unansweredQuestion.id}`)
              "
            />
          </div>
        </article>
      </section>
      <footer class="composer">
        <div
          v-if="(!modelOptions.length && !listLoading) || sending || activeRun || preparing"
          class="composer-context"
        >
          <span v-if="!modelOptions.length && !listLoading" class="muted small"
            >暂无可用模型，请联系管理员配置</span
          >
          <div class="composer-context-actions">
            <span v-if="sending || activeRun || preparing" class="run-state" role="status">{{
              preparing
                ? '正在检索知识库'
                : sending
                  ? '提交中'
                  : activeRun
                    ? runStatusLabel[activeRun.status]
                    : ''
            }}</span>
          </div>
        </div>
        <div v-if="selectedSkills.length" class="selected-skills">
          <ATag
            v-for="skill in selectedSkills"
            :key="skill.skillSlug"
            :closable="!running && !sending"
            @close="removeSkill(skill.skillSlug)"
            >技能：{{ skill.skillName }}</ATag
          >
        </div>
        <div v-if="readOnly" class="archived-actions">
          <span class="archived-message">{{
            selected?.archived
              ? '会话已归档，取消归档后可继续对话。'
              : '会话状态待核对，请刷新会话。'
          }}</span>
        </div>
        <div v-if="queuedFiles.length" class="file-queue" aria-label="待发送附件">
          <div
            v-for="file in queuedFiles"
            :key="file.id"
            class="queued-file"
            :class="{ 'has-error': file.status === 'failed' }"
          >
            <img
              v-if="file.previewUrl"
              :src="file.previewUrl"
              :alt="file.fileName"
              class="queued-file-preview"
            /><PictureOutlined
              v-else-if="file.kind === 'image'"
              class="queued-file-icon"
            /><FileTextOutlined v-else class="queued-file-icon" />
            <div class="queued-file-details">
              <span class="queued-file-name">{{ file.fileName }}</span
              ><span class="queued-file-status">{{
                file.status === 'ready'
                  ? '上传完成'
                  : file.status === 'uploading'
                    ? '上传中'
                    : '上传失败'
              }}</span
              ><span v-if="file.error" class="queued-file-error">{{ file.error }}</span>
            </div>
            <AButton
              v-if="file.status === 'failed'"
              size="small"
              :disabled="sending || running || readOnly"
              @click="retryFile(file.id)"
              >重新上传</AButton
            ><AButton
              type="text"
              :aria-label="`移除附件：${file.fileName}`"
              :disabled="sending || running"
              @click="removeFile(file.id)"
              ><CloseOutlined
            /></AButton>
          </div>
        </div>
        <input
          ref="fileInput"
          class="chat-file-input"
          type="file"
          :accept="CHAT_FILE_ACCEPT"
          multiple
          aria-label="选择附件文件"
          @change="chooseFiles"
        />
        <div class="composer-surface">
          <ATextarea
            v-model:value="draft"
            :auto-size="{ minRows: 3, maxRows: 6 }"
            :maxlength="16000"
            :disabled="sending || preparing || readOnly"
            placeholder="输入消息，按回车发送，按住上档键回车换行"
            aria-label="消息内容"
            @keydown="keydown"
          />
          <div class="composer-actions">
            <div class="composer-tools">
              <SkillPicker
                v-model="selectedSkills"
                v-model:open="skillPickerOpen"
                :disabled="running || sending || loading"
                @selected="handleSkillSelected"
              />
              <AButton
                type="text"
                :disabled="sending || running || loading || readOnly"
                aria-label="添加图片或附件"
                title="每条消息最多10张图片、5个文档，单个文件最大20MiB"
                @click="fileInput?.click()"
                ><PaperClipOutlined /></AButton
              ><span class="composer-hint">{{
                draft.length ? `${draft.length} / 16000` : '单个文件最大20MiB'
              }}</span>
            </div>
            <div class="composer-controls">
              <ASelect
                :value="modelId"
                class="model-select"
                :options="modelOptions"
                show-search
                option-filter-prop="label"
                aria-label="选择模型"
                placeholder="选择平台模型"
                :disabled="running || sending || loading"
                @update:value="setModelId"
              />
              <AButton
                v-if="running"
                class="composer-submit"
                danger
                :loading="stopping"
                :disabled="stopAcknowledged"
                aria-label="停止生成"
                @click="stop"
                ><PauseCircleOutlined />停止</AButton
              ><AButton
                v-else
                class="composer-submit"
                type="primary"
                :loading="sending"
                :disabled="
                  (!draft.trim() && !queuedFiles.length) ||
                  !modelId ||
                  loading ||
                  listLoading ||
                  !historyReady ||
                  !fileQueueReady ||
                  regenerationPending ||
                  readOnly
                "
                aria-label="发送消息"
                @click="send()"
                ><SendOutlined />发送</AButton
              >
            </div>
          </div>
        </div>
      </footer>
    </main>
    <MessageShareDialog
      v-if="shareMessageId"
      :message-id="shareMessageId"
      @close="shareMessageId = null"
    />
  </div>
</template>

<style scoped lang="scss">
.reply-versions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.reply-versions button {
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: inherit;
  font-size: 22px;
  cursor: pointer;
}
.reply-versions button:hover {
  background: var(--gray-50);
}
.reply-versions button:disabled {
  opacity: 0.4;
  cursor: default;
}
.reply-versions button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.archived-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.archived-message {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.platform-chat {
  position: relative;
  height: 100%;
  display: flex;
  overflow: hidden;
  color: var(--color-text);
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.run-state,
.message-meta,
.message-metrics,
.composer-hint {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.message-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.message-run-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-secondary);
}
.message-run-status::before {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: currentColor;
  content: '';
}
.message-run-status.is-failed {
  color: var(--color-error-700);
}
.message-body .run-error,
.empty-reply {
  margin: 10px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
}

.chat-workspace {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  background: var(--bg-container);
}

.toolbar-icon-button {
  margin-left: auto;
  flex-shrink: 0;
  width: var(--workspace-control-height);
  height: var(--workspace-control-height);
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.model-select {
  flex: 1;
  width: min(220px, 100%);
  min-width: 0;
}

.chat-toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  min-height: 70px;
  padding: 12px 24px;
}
.chat-toolbar-action {
  flex-shrink: 0;
}
button.chat-toolbar-action {
  display: inline-flex;
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
button.chat-toolbar-action:hover,
.toolbar-icon-button:hover {
  background: var(--gray-50);
}
button.chat-toolbar-action:focus-visible,
.toolbar-icon-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
@media (max-width: 600px) {
  .chat-toolbar__label {
    display: none;
  }
  .chat-toolbar-action {
    width: var(--workspace-control-height);
    padding: 0;
  }
  button.chat-toolbar-action {
    padding: 0;
  }
}

.composer-context {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-bottom: 8px;
}

.composer-context-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.message-history {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px max(24px, calc((100% - 800px) / 2));
  background: transparent;
}

.message-row {
  align-items: flex-start;
  display: flex;
  gap: 12px;
  max-width: 800px;
  margin: 0 auto 22px;
}

.message-row.user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 30px;
  height: 30px;
  display: grid;
  place-items: center;
  flex: 0 0 30px;
  border-radius: 8px;
  color: var(--main-color);
  background: var(--gray-150);
}

.message-body {
  min-width: 0;
  max-width: min(760px, 90%);
}

.message-row.assistant .message-body {
  flex: 1;
}

.message-row.user .message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  text-align: left;
}

.user-message {
  margin: 5px 0 0;
  padding: 10px 13px;
  max-width: 100%;
  border: 0;
  border-radius: 10px 0 10px 10px;
  background: var(--gray-50);
  color: var(--color-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
}

.message-skills,
.selected-skills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}

.message-row.user .message-skills {
  justify-content: flex-end;
}

.selected-skills {
  flex-shrink: 0;
  margin: 0 0 8px;
}

.chat-file-input {
  display: none;
}
.file-queue {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 60px;
  margin-bottom: 10px;
  max-height: 220px;
  overflow-y: auto;
}
.queued-file {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  width: min(100%, 380px);
  padding: 8px;
  border: 1px solid var(--gray-200);
  border-radius: 10px;
  background: var(--bg-container);
}
.queued-file.has-error {
  border-color: var(--ant-color-error, #ff4d4f);
}
.queued-file-preview {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: 6px;
  flex: 0 0 40px;
}
.queued-file-icon {
  font-size: 24px;
  color: var(--color-text-secondary);
}
.queued-file-details {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  flex: 1;
}
.queued-file-name {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.queued-file-status,
.queued-file-error {
  font-size: var(--workspace-font-size-caption);
  overflow-wrap: anywhere;
}
.queued-file-status {
  color: var(--color-text-secondary);
}
.queued-file-error,
.file-guidance--error {
  color: var(--ant-color-error, #ff4d4f);
}
.file-guidance {
  margin: 8px 4px 0;
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-helper);
  line-height: 1.5;
}
.file-guidance--error {
  color: var(--ant-color-error, #ff4d4f);
}
.message-row.user :deep(.message-files) {
  justify-content: flex-end;
}

.message-metrics {
  margin-top: 6px;
}

.composer {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  min-height: 0;
  // Reserve the 52px toolbar and 80px for the message history.
  max-height: calc(100% - 132px);
  overflow-y: auto;
  width: min(880px, 100%);
  align-self: center;
  padding: 10px 24px 8px;
  background: transparent;
}

.composer-surface {
  flex-shrink: 0;
  padding: 12px 13px 10px;
  border: 1px solid var(--gray-200);
  border-radius: 13px;
  background: var(--bg-container);
  box-shadow: none;
  transition:
    border-color 0.18s,
    box-shadow 0.18s;
}

.composer-surface:focus-within {
  border-color: var(--main-400);
  box-shadow:
    0 0 0 3px var(--main-50),
    0 6px 24px rgb(0 0 0 / 4%);
}

.composer-surface :deep(.ant-input) {
  padding: 4px;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.composer :deep(textarea) {
  font-size: var(--workspace-font-size-chat);
  line-height: var(--workspace-line-height-chat);
}

.composer-actions {
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 8px;
  margin-top: 8px;
}

.composer-tools,
.composer-controls {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.composer-tools {
  flex: 1 1 auto;
}

.composer-controls {
  flex: 0 1 304px;
}

.composer-hint {
  margin-left: auto;
}

.composer-actions :deep(.ant-upload-wrapper),
.composer-actions :deep(.ant-upload) {
  display: inline-flex;
}

.composer-submit {
  flex-shrink: 0;
  width: 84px;
  height: 36px;
  border-radius: 10px;
}

.model-select :deep(.ant-select-selector) {
  border-color: transparent;
  border-radius: 10px;
  background: transparent;
  box-shadow: none;
}
.composer-actions :deep(.ant-btn-text) {
  border-radius: 9px;
  color: var(--color-text-secondary);
}

:deep(.ant-form-item .ant-input-number) {
  width: 100%;
}

@media (max-width: 760px) {
  .chat-workspace {
    padding: 0;
  }
  .chat-toolbar,
  .composer {
    padding-right: 14px;
    padding-left: 14px;
  }

  .message-history {
    padding: 16px 12px;
  }
  .composer-tools,
  .composer-controls {
    flex-basis: 100%;
  }
}

@media (max-width: 480px) {
  .composer-surface {
    padding: 12px;
    border-radius: 16px;
  }
  .composer-actions {
    gap: 5px;
  }
  .composer-submit {
    width: 76px;
  }
}
.chat-alert {
  padding: 0 24px;
}
.chat-alert :deep(.ant-alert) {
  margin-bottom: 8px;
}
.chat-alert :deep(.ant-btn) {
  margin: 0 8px 8px 0;
}
.chat-session-heading {
  padding: 10px 28px 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
}
@media (max-width: 760px) {
  .message-history {
    padding: 18px;
  }
  .message-actions {
    gap: 6px;
  }
}
.is-empty .message-history {
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
