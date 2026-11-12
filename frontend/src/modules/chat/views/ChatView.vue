<!-- Adapted from the original chat/index.vue, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useResizeObserver, useScroll } from '@vueuse/core'
import {
  Alert as AAlert,
  Button as AButton,
  Select as ASelect,
  Spin as ASpin,
  Textarea as ATextarea
} from 'ant-design-vue'
import {
  HistoryOutlined,
  PlusOutlined,
  CloseOutlined,
  LeftOutlined,
  RightOutlined,
  FileTextOutlined,
  PictureOutlined,
  AppstoreOutlined,
  ArrowUpOutlined,
  BorderOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import { runStatusLabel } from '../../../shared/stream.ts'
import { useChatWorkspace } from '../public.ts'
import ChatHistoryDialog from '../components/ChatHistoryDialog.vue'
import ChatGenerationSettingsDialog from '../components/ChatGenerationSettingsDialog.vue'
import MessageActions from '../../../shared/components/MessageActions.vue'
import { SkillIdentityIcon, SkillPicker } from '../../skills/public.ts'
import { formatFileSize } from '../../files/public.ts'
import { KnowledgeSources } from '../../knowledge/public.ts'
import AiMessage from '../components/AiMessage.vue'
import ChatEmptyState from '../components/ChatEmptyState.vue'
import ChatMessageFiles from '../components/ChatMessageFiles.vue'
import MessageShareDialog from '../components/MessageShareDialog.vue'
import { groupMessageVersions } from '../composables/messageVersions.ts'
import type { ChatMessage } from '../api/index.ts'
import { CHAT_FILE_ACCEPT } from '../attachments.ts'
const {
  contextInfo,
  contextNotice,
  contextRetrying,
  retryChatContext,
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
const showRunStatus = computed(
  () =>
    activeRun.value !== null &&
    activeRun.value.status !== 'running' &&
    activeRun.value.status !== 'completed'
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
const composerSurface = ref<HTMLElement | null>(null)
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
  composerSurface.value?.querySelector('textarea')?.focus()
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
function attachmentType(name: string): string {
  return name.split('.').pop()?.toUpperCase() || '文件'
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
      <div v-if="contextNotice" class="chat-context-notice" role="status">
        <span>{{ contextNotice }}</span>
        <AButton
          v-if="contextInfo?.status === 'degraded' && !readOnly"
          type="link"
          size="small"
          :loading="contextRetrying"
          title="使用当前模型和积分重新整理较早对话"
          @click="retryChatContext"
        >
          重试整理
        </AButton>
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
            <div v-if="isActive(row.message) && activeRun && showRunStatus" class="message-meta">
              <span class="message-run-status" :class="`is-${activeRun.status}`">{{
                replyStatus
              }}</span>
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
        </aside>
        <div
          v-if="(!modelOptions.length && !listLoading) || sending || preparing || showRunStatus"
          class="composer-context"
        >
          <span v-if="!modelOptions.length && !listLoading" class="muted small"
            >暂无可用模型，请联系管理员配置</span
          >
          <div class="composer-context-actions">
            <span v-if="sending || preparing || showRunStatus" class="run-state" role="status">{{
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
        <div v-if="readOnly" class="archived-actions">
          <span class="archived-message">{{
            selected?.archived
              ? '会话已归档，取消归档后可继续对话。'
              : '会话状态待核对，请刷新会话。'
          }}</span>
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
        <div
          ref="composerSurface"
          class="composer-surface"
          :class="{ 'is-selecting-skills': skillPickerOpen }"
        >
          <SkillPicker
            ref="skillPicker"
            v-model="selectedSkills"
            v-model:open="skillPickerOpen"
            :disabled="running || sending || preparing || loading || readOnly"
            @selected="handleSkillSelected"
            @dismiss="focusDraft"
          />
          <div class="composer-input-area">
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
                  />
                  <PictureOutlined v-else-if="file.kind === 'image'" class="queued-file-icon" />
                  <FileTextOutlined v-else class="queued-file-icon" />
                  <div class="queued-file-details">
                    <span class="queued-file-name" :title="file.fileName">{{ file.fileName }}</span>
                    <span class="queued-file-status" role="status">{{
                      file.status === 'ready'
                        ? `${attachmentType(file.fileName)} · ${formatFileSize(file.sizeBytes)}`
                        : file.status === 'uploading'
                          ? '上传中…'
                          : '上传失败'
                    }}</span>
                    <span v-if="file.error" class="queued-file-error">{{ file.error }}</span>
                  </div>
                  <AButton
                    v-if="file.status === 'failed'"
                    size="small"
                    :disabled="sending || running || readOnly"
                    @click="retryFile(file.id)"
                    >重新上传</AButton
                  >
                  <AButton
                    type="text"
                    :aria-label="`移除附件：${file.fileName}`"
                    :disabled="sending || running"
                    @click="removeFile(file.id)"
                    ><CloseOutlined
                  /></AButton>
                </div>
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
                  :disabled="running || sending || preparing || readOnly"
                  @click="removeSkill(skill.skillSlug)"
                >
                  <CloseOutlined aria-hidden="true" />
                </button>
              </span>
            </div>
            <ATextarea
              v-model:value="draft"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              :maxlength="16000"
              :disabled="sending || preparing || readOnly"
              placeholder="输入消息，或选择技能开始"
              aria-label="消息内容"
              @keydown="composerKeydown"
            />
          </div>
          <div class="composer-actions">
            <div class="composer-tools">
              <AButton
                type="text"
                :disabled="sending || running || loading || readOnly"
                aria-label="添加图片或附件"
                title="每条消息附件合计最多50MB，单个文件最大20MiB"
                @click="fileInput?.click()"
                ><PlusOutlined /><span class="composer-tool-label">添加附件</span></AButton
              >
              <button
                type="button"
                class="composer-skill-trigger"
                :class="{ 'is-active': skillPickerOpen }"
                :disabled="running || sending || preparing || loading || readOnly"
                aria-label="选择技能"
                :aria-expanded="skillPickerOpen"
                title="选择技能，也可输入 @"
                @click="skillPickerOpen = !skillPickerOpen"
              >
                <AppstoreOutlined aria-hidden="true" /><span class="composer-tool-label">技能</span>
              </button>
              <span v-if="draft.length >= 14000" class="composer-hint"
                >{{ draft.length }} / 16000</span
              >
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
                title="发送消息（Enter），Shift + Enter 换行"
                @click="send()"
                ><ArrowUpOutlined
              /></AButton>
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
  --workspace-font-size-chat: 17px;
  --workspace-line-height-chat: 1.8;
  --chat-column-width: 920px;
  --chat-gutter: 24px;
  --chat-composer-padding: 20px;
  --chat-inset: calc(var(--chat-gutter) + var(--chat-composer-padding) + 1px);
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
  margin-bottom: 10px;
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
.message-history {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px max(var(--chat-inset), calc((100% - var(--chat-column-width)) / 2));
  background: transparent;
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
}

.message-row.assistant .message-body {
  flex: 1;
}

.message-row.user .message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  text-align: left;
  max-width: 85%;
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
.queued-file {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
  min-width: 0;
  width: min(100%, 240px);
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--gray-25);
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
  font-size: 18px;
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
  font-size: 14px;
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
  // Keep the toolbar, conversation heading and part of the history visible.
  max-height: calc(100% - 180px);
  width: min(calc(var(--chat-column-width) + var(--chat-inset) * 2), 100%);
  align-self: center;
  padding: 10px var(--chat-gutter) 12px;
  background: transparent;
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
  box-shadow: none;
  transition:
    border-color 0.18s,
    box-shadow 0.18s;
}

.composer-surface:focus-within,
.composer-surface.is-selecting-skills {
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
  flex-shrink: 0;
  justify-content: flex-start;
  gap: 8px;
  padding: 10px var(--chat-composer-padding) 12px;
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
  flex: 0 1 260px;
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
  .platform-chat {
    --chat-gutter: 14px;
    --chat-composer-padding: 16px;
  }
  .chat-workspace {
    padding: 0;
  }
  .chat-toolbar,
  .composer {
    padding-right: 14px;
    padding-left: 14px;
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
.chat-session-heading {
  padding: 10px 28px 0;
  color: var(--color-text-tertiary);
  font-size: 12px;
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
