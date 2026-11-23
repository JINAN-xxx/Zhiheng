<!-- Adapted from the original chat/index.vue, MIT licensed. -->
<script setup lang="ts">
import { canUseCapability } from '../../identity/public.ts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useResizeObserver, useScroll } from '@vueuse/core'
import {
  Alert as AAlert,
  Button as AButton,
  Select as ASelect,
  Spin as ASpin
} from 'ant-design-vue'
import {
  HistoryOutlined,
  PlusOutlined,
  CloseOutlined,
  LeftOutlined,
  RightOutlined,
  AppstoreOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import { runStatusLabel } from '../../../shared/stream.ts'
import { useChatWorkspace } from '../public.ts'
import ChatHistoryDialog from '../components/ChatHistoryDialog.vue'
import ChatGenerationSettingsDialog from '../components/ChatGenerationSettingsDialog.vue'
import MessageActions from '../../../shared/components/MessageActions.vue'
import ConversationComposer from '../../../shared/components/ConversationComposer.vue'
import { SkillIdentityIcon, SkillPicker } from '../../skills/public.ts'
import { formatFileSize } from '../../files/public.ts'
import AiMessage from '../components/AiMessage.vue'
import ChatEmptyState from '../components/ChatEmptyState.vue'
import ChatMessageFiles from '../components/ChatMessageFiles.vue'
import MessageShareDialog from '../components/MessageShareDialog.vue'
import { useChatMessagePresentation } from '../composables/messagePresentation.ts'
import { CHAT_FILE_ACCEPT } from '../attachments.ts'
import AttachmentButton from '../../../shared/components/AttachmentButton.vue'
import AttachmentCard from '../../../shared/components/AttachmentCard.vue'
const {
  canUseChat,
  contextNotice,
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
  skillPickerOpen,
  removeSkill,
  handleSkillSelected,
  error,
  inputIssue,
  dismissInputIssue,
  listError,
  loading,
  listLoading,
  historyReady,
  olderCursor,
  olderLoading,
  olderError,
  loadOlderMessages,
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
const {
  selectedVersions,
  messageRows,
  activeRunRepresented,
  unansweredQuestion,
  replyStatus,
  showRunStatus,
  shareMessageId,
  isActive,
  messageText,
  messageStatus,
  regenerateReply
} = useChatMessagePresentation({
  messages,
  activeRun,
  selected,
  regenerate
})
const historyOpen = ref(false)
const settingsOpen = ref(false)
async function newConversation(): Promise<void> {
  await selectConversation(null)
}
const fileInput = ref<HTMLInputElement | null>(null)
const composerSurface = ref<InstanceType<typeof ConversationComposer> | null>(null)
const fileQueue = ref<HTMLElement | null>(null)
const queuedFileBytes = computed(() =>
  queuedFiles.value.reduce((total, file) => total + file.sizeBytes, 0)
)
const fileQueueScroll = useScroll(fileQueue, {
  offset: { left: 2, right: 2 }
})
const fileQueueEdges = fileQueueScroll.arrivedState
const measureFileQueue = () => fileQueueScroll.measure()
useResizeObserver(fileQueue, measureFileQueue)
watch(() => queuedFiles.value.length, measureFileQueue, { flush: 'post' })
function scrollFiles(direction: -1 | 1): void {
  const element = fileQueue.value
  if (!element) return
  element.scrollBy({
    left: direction * Math.max(240, element.clientWidth - 48),
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  })
}
const skillPicker = ref<{ handleKeydown: (event: KeyboardEvent) => boolean } | null>(null)
function focusDraft(): void {
  composerSurface.value?.focus()
}
function composerKeydown(event: KeyboardEvent): void {
  if (event.isComposing || event.keyCode === 229) return
  if (skillPickerOpen.value && skillPicker.value?.handleKeydown(event)) return
  keydown(event)
}
function closeSkillsOutside(event: PointerEvent): void {
  if (event.target instanceof Node && !composerSurface.value?.contains(event.target))
    skillPickerOpen.value = false
}
onMounted(() => document.addEventListener('pointerdown', closeSkillsOutside))
function chooseFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.files) addFiles(Array.from(input.files))
  input.value = ''
}
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', closeSkillsOutside)
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
      :disabled="!canUseChat || running || sending || loading || listLoading || readOnly"
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
          v-if="canUseChat"
          type="text"
          class="chat-toolbar-action"
          aria-label="新建对话"
          :disabled="sending"
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
      <div v-if="contextNotice" class="chat-context-notice" role="status">
        <span>{{ contextNotice }}</span>
      </div>
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
        <AButton v-if="canUseChat && canRetry" size="small" @click="send(true)"
          >使用原请求重试</AButton
        ><AButton v-if="canUseChat && canRetryRegeneration" size="small" @click="retryRegeneration"
          >使用原重新生成请求重试</AButton
        ><AButton v-if="canRecover" size="small" @click="reconnect">恢复查看</AButton>
      </div>
      <section
        ref="messagesElement"
        class="message-history"
        aria-live="polite"
        aria-label="聊天消息"
      >
        <div v-if="olderCursor || olderError" class="message-history__earlier">
          <AButton v-if="olderCursor" type="text" :loading="olderLoading" @click="loadOlderMessages"
            >加载更早消息</AButton
          >
          <span v-if="olderError" role="alert">{{ olderError }}</span>
        </div>
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
          <div class="message-body">
            <div v-if="messageStatus(row.message)" class="message-meta">
              <span class="message-run-status">{{ messageStatus(row.message) }}</span>
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
            <div v-if="row.message.skill_snapshots.length" class="message-skills">
              <span
                v-for="skill in row.message.skill_snapshots"
                :key="skill.revision_id"
                class="message-skill"
              >
                <SkillIdentityIcon :slug="skill.slug" plain />
                <span class="message-skill-name" :title="skill.name">{{ skill.name }}</span>
              </span>
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
              :regenerate="canUseChat && row.message.role === 'assistant'"
              :feedback-enabled="row.message.role === 'assistant' && !row.message.incomplete_reason"
              :share="
                canUseCapability('chat', 'share') &&
                row.message.role === 'assistant' &&
                !row.message.incomplete_reason
              "
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
          <div class="message-body">
            <div v-if="showRunStatus" class="message-meta">
              <span class="message-run-status" :class="`is-${activeRun.status}`">{{
                replyStatus
              }}</span>
            </div>
            <AiMessage
              :content="activeRun.text"
              :reasoning-content="activeRun.reasoning ?? ''"
              :loading="running"
            />
            <p v-if="activeRun.error" class="run-error">{{ activeRun.error.message }}</p>
            <span v-if="activeRun.usage.status === 'unknown'" class="muted small">用量待核对</span>
            <p v-if="!running && !activeRun.text && !activeRun.error" class="empty-reply">
              {{ activeRun.status === 'cancelled' ? '已停止生成' : '本次回复未生成内容' }}
            </p>
            <MessageActions
              v-if="activeRun.text || unansweredQuestion"
              :content="activeRun.text"
              :regenerate="canUseChat && Boolean(unansweredQuestion)"
              :regenerate-reason="unansweredQuestion ? regenerationReason(unansweredQuestion) : ''"
              @regenerate="
                unansweredQuestion &&
                regenerateReply(unansweredQuestion, `reply:${unansweredQuestion.id}`)
              "
            />
          </div>
        </article>
      </section>
      <ConversationComposer
        v-if="canUseChat || running"
        ref="composerSurface"
        v-model="draft"
        :disabled="!canUseChat || sending || readOnly"
        :running="running"
        :sending="sending"
        :stopping="stopping"
        :stop-disabled="stopAcknowledged"
        :selecting-skills="skillPickerOpen"
        :submit-disabled="
          (!draft.trim() && !queuedFiles.length) ||
          !modelId ||
          loading ||
          listLoading ||
          !historyReady ||
          !fileQueueReady ||
          regenerationPending ||
          canRetry ||
          readOnly
        "
        @keydown="composerKeydown"
        @send="send()"
        @stop="stop"
      >
        <template #notice>
          <aside v-if="inputIssue" class="composer-notice" role="alert" aria-label="发送提示">
            <AAlert
              type="warning"
              :message="inputIssue.title"
              show-icon
              closable
              @close="dismissInputIssue"
            >
              <template #description>
                <p>{{ inputIssue.description }}</p>
                <small v-if="inputIssue.statistics">{{ inputIssue.statistics }}</small>
                <div class="composer-notice-actions">
                  <AButton type="link" size="small" @click="focusDraft">调整内容</AButton>
                  <AButton type="link" size="small" @click="settingsOpen = true">对话设置</AButton>
                </div>
              </template>
            </AAlert>
          </aside></template
        >
        <template #context>
          <div
            v-if="(!modelOptions.length && !listLoading) || sending || showRunStatus"
            class="composer-context"
          >
            <span v-if="!modelOptions.length && !listLoading" class="muted small"
              >暂无可用模型，请联系管理员配置</span
            >
            <div class="composer-context-actions">
              <span v-if="sending || showRunStatus" class="run-state" role="status">{{
                sending ? '提交中' : activeRun ? runStatusLabel[activeRun.status] : ''
              }}</span>
            </div>
          </div>
          <div v-if="readOnly" class="archived-actions">
            <span class="archived-message">{{
              selected?.archived
                ? '会话已归档，取消归档后可继续对话。'
                : '会话状态待核对，请刷新会话。'
            }}</span>
          </div>
        </template>
        <template #before-surface>
          <input
            ref="fileInput"
            class="chat-file-input"
            type="file"
            :accept="CHAT_FILE_ACCEPT"
            multiple
            aria-label="选择附件文件"
            @change="chooseFiles"
          />
        </template>
        <template #overlay>
          <SkillPicker
            v-if="canUseChat && canUseCapability('skills', 'use')"
            ref="skillPicker"
            v-model="selectedSkills"
            v-model:open="skillPickerOpen"
            :disabled="running || sending || loading || readOnly"
            @selected="handleSkillSelected"
            @dismiss="focusDraft"
        /></template>
        <template #prefix>
          <div
            v-if="queuedFiles.length"
            class="file-queue-container"
            :class="{ 'has-previous': !fileQueueEdges.left, 'has-next': !fileQueueEdges.right }"
          >
            <div
              ref="fileQueue"
              class="file-queue"
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
                :disabled="sending || running"
                :retry-disabled="readOnly"
                @retry="retryFile(file.id)"
                @remove="removeFile(file.id)"
              />
            </div>
            <button
              v-if="!fileQueueEdges.left"
              class="file-queue-scroll file-queue-scroll--previous"
              type="button"
              aria-label="向左查看附件"
              @click="scrollFiles(-1)"
            >
              <LeftOutlined aria-hidden="true" />
            </button>
            <button
              v-if="!fileQueueEdges.right"
              class="file-queue-scroll file-queue-scroll--next"
              type="button"
              aria-label="向右查看附件"
              @click="scrollFiles(1)"
            >
              <RightOutlined aria-hidden="true" />
            </button>
          </div>
          <p v-if="queuedFiles.length" class="file-queue-usage" aria-label="附件总大小">
            {{ formatFileSize(queuedFileBytes) }} / 50 MB
          </p>
          <div v-if="selectedSkills.length" class="selected-skills" aria-label="已选技能">
            <span v-for="skill in selectedSkills" :key="skill.skillSlug" class="selected-skill">
              <SkillIdentityIcon :slug="skill.skillSlug" plain />
              <span class="selected-skill-name" :title="skill.skillName">{{
                skill.skillName
              }}</span>
              <button
                type="button"
                :aria-label="`移除技能：${skill.skillName}`"
                :disabled="running || sending || readOnly"
                @click="removeSkill(skill.skillSlug)"
              >
                <CloseOutlined aria-hidden="true" />
              </button>
            </span>
          </div>
        </template>
        <template #tools>
          <AttachmentButton
            v-if="canUseChat"
            :disabled="sending || running || loading || readOnly"
            aria-label="添加图片或附件"
            title="每条消息附件合计最多50MB，单个文件最大20MiB"
            @click="fileInput?.click()"
          />
          <button
            v-if="canUseChat && canUseCapability('skills', 'use')"
            type="button"
            class="composer-skill-trigger"
            :class="{ 'is-active': skillPickerOpen }"
            :disabled="running || sending || loading || readOnly"
            aria-label="选择技能"
            :aria-expanded="skillPickerOpen"
            title="选择技能，也可输入 @"
            @click="skillPickerOpen = !skillPickerOpen"
          >
            <AppstoreOutlined aria-hidden="true" /><span class="composer-tool-label">技能</span>
          </button>
          <span v-if="draft.length >= 14000" class="composer-hint">{{ draft.length }} / 16000</span>
        </template>
        <template #controls>
          <ASelect
            :value="modelId"
            class="model-select"
            :options="modelOptions"
            show-search
            option-filter-prop="label"
            aria-label="选择模型"
            placeholder="选择平台模型"
            :disabled="!canUseChat || running || sending || loading"
            @update:value="setModelId"
        /></template>
      </ConversationComposer>
    </main>
    <MessageShareDialog
      v-if="shareMessageId"
      :message-id="shareMessageId"
      @close="shareMessageId = null"
    />
  </div>
</template>

<style scoped lang="scss">
@use '../../../shared/styles/conversation' as conversation;
@include conversation.page('.platform-chat');
@include conversation.toolbar;
@include conversation.history;
@include conversation.messages;

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

.run-state,
.message-meta,
.message-metrics,
.composer-hint {
  color: var(--color-text-secondary);
  font-size: 12px;
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

.toolbar-icon-button:hover {
  background: var(--gray-50);
}
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
.composer-notice {
  flex-shrink: 0;
  margin-bottom: 10px;
}
.composer-notice :deep(.ant-alert) {
  border-radius: 12px;
}
.composer-notice p {
  margin: 0;
}
.composer-notice small {
  display: block;
  margin-top: 6px;
  color: var(--color-text-secondary);
}
.composer-notice :deep(.ant-alert-icon) {
  font-size: 18px;
}
.composer-notice-actions {
  display: flex;
  gap: 12px;
  margin-top: 6px;
}
.composer-notice-actions :deep(.ant-btn) {
  padding-inline: 0;
}

.composer-context-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.message-history__earlier {
  text-align: center;
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}

.message-skills,
.selected-skills {
  display: flex;
  max-width: 100%;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}

.message-row.user .message-skills {
  justify-content: flex-end;
}

.selected-skills {
  flex-shrink: 0;
  gap: 8px 12px;
  margin: 0 0 12px;
}
.selected-skill,
.message-skill {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 6px;
  color: var(--main-700);
  font-size: 15px;
}
.message-skill {
  font-size: 13px;
}
.selected-skill-name {
  max-width: 180px;
}
.selected-skill-name,
.message-skill-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.selected-skill button {
  display: grid;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  place-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 12px;
  cursor: pointer;
}
.selected-skill button:hover {
  background: var(--main-50);
  color: var(--main-700);
}
.selected-skill button:disabled {
  opacity: 0.5;
  cursor: default;
}

.chat-file-input {
  display: none;
}
.file-queue-container {
  position: relative;
  min-width: 0;
  margin-bottom: 6px;
}
.file-queue-usage {
  margin: 0 2px 12px;
  color: var(--color-text-tertiary);
  font-size: var(--workspace-font-size-caption);
}
.file-queue {
  display: flex;
  gap: 8px;
  min-width: 0;
  padding: 2px;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-padding-inline: 36px;
  scrollbar-width: none;
}
.file-queue::-webkit-scrollbar {
  display: none;
}
.file-queue-container.has-previous::before,
.file-queue-container.has-next::after {
  position: absolute;
  z-index: 1;
  top: 0;
  bottom: 0;
  width: 40px;
  pointer-events: none;
  content: '';
}
.file-queue-container.has-previous::before {
  left: 0;
  background: linear-gradient(to right, var(--bg-container), transparent);
}
.file-queue-container.has-next::after {
  right: 0;
  background: linear-gradient(to left, var(--bg-container), transparent);
}
.file-queue-scroll {
  position: absolute;
  z-index: 2;
  top: 50%;
  display: grid;
  width: 28px;
  height: 28px;
  padding: 0;
  place-items: center;
  border: 1px solid var(--gray-150);
  border-radius: 50%;
  background: var(--bg-container);
  color: var(--color-text-secondary);
  box-shadow: 0 2px 6px rgb(0 0 0 / 8%);
  font-size: 12px;
  cursor: pointer;
  transform: translateY(-50%);
}
.file-queue-scroll--previous {
  left: 4px;
}
.file-queue-scroll--next {
  right: 4px;
}
.file-queue-scroll:hover {
  color: var(--main-700);
  border-color: var(--main-300);
}
.file-queue:focus-visible,
.file-queue-scroll:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}
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

.composer-hint {
  margin-left: auto;
}

.composer-actions :deep(.ant-upload-wrapper),
.composer-actions :deep(.ant-upload) {
  display: inline-flex;
}

.composer-skill-trigger {
  display: inline-flex;
  height: 36px;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 4px 10px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.composer-skill-trigger:hover {
  background: var(--gray-50);
}
.composer-skill-trigger.is-active {
  background: var(--main-30);
  color: var(--main-700);
}
.composer-skill-trigger:disabled {
  color: var(--color-text-tertiary);
  cursor: not-allowed;
}

:deep(.ant-form-item .ant-input-number) {
  width: 100%;
}

@media (max-width: 760px) {
  .chat-workspace {
    padding: 0;
  }
}

@media (max-width: 600px) {
  .composer-tool-label {
    display: none;
  }
  .composer-tools {
    flex: 0 0 auto;
    gap: 2px;
  }
  .composer-controls {
    flex: 1;
  }
  .composer-tools :deep(.ant-btn-text),
  .composer-skill-trigger {
    width: 32px;
    padding: 0;
  }
  .composer-actions {
    gap: 5px;
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
@media (max-width: 760px) {
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

<style scoped>
.chat-context-notice {
  padding: 2px 24px 8px;
  color: #64748b;
  font-size: 12px;
}
</style>
