<!-- Adapted from the original chat/index.vue, MIT licensed. -->
<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
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
import { SkillPicker } from '../../skills/public.ts'
import { KnowledgePicker, KnowledgeSources } from '../../knowledge/public.ts'
import AiMessage from '../components/AiMessage.vue'
import ChatEmptyState from '../components/ChatEmptyState.vue'
import ChatMessageFiles from '../components/ChatMessageFiles.vue'
import { CHAT_FILE_ACCEPT } from '../attachments.ts'
const {
  selectConversation,
  selected,
  queuedFiles,
  addFiles,
  removeFile,
  retryFile,
  fileQueueReady,
  supportsVision,
  readOnly,
  modelId,
  messages,
  draft,
  selectedSkills,
  selectedKnowledgeBases,
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
  visibleMessages,
  canRetry,
  send,
  stop,
  reconnect,
  setModelId,
  keydown
} = useChatWorkspace()
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
    <main class="chat-workspace">
      <header class="chat-toolbar">
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
          class="toolbar-icon-button"
          type="text"
          disabled
          aria-label="对话偏好"
          title="当前版本暂不支持修改对话偏好"
          ><SettingOutlined
        /></AButton>
      </header>
      <div v-if="error || listError || canRetry || canRecover" class="chat-alert">
        <AAlert v-if="error" :message="error" type="error" show-icon /><AAlert
          v-if="listError"
          :message="listError"
          type="error"
          show-icon
        />
        <AButton v-if="canRetry" size="small" @click="send(true)">使用原请求重试</AButton
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
          v-for="message in visibleMessages"
          :key="message.id"
          class="message-row"
          :class="message.role"
        >
          <AAvatar class="message-avatar" :size="30"
            ><UserOutlined v-if="message.role === 'user'" /><RobotOutlined v-else
          /></AAvatar>
          <div class="message-body">
            <div class="message-meta">{{ message.role === 'user' ? '我' : '智衡' }}</div>
            <AiMessage
              v-if="message.role === 'assistant'"
              :content="message.content"
              :reasoning-content="message.reasoning"
            />
            <p v-else class="user-message">{{ message.content }}</p>
            <ChatMessageFiles :files="message.attachments" />
            <KnowledgeSources :sources="message.knowledge_sources" />
            <div v-if="message.skill_snapshots.length" class="message-skills">
              <ATag v-for="skill in message.skill_snapshots" :key="skill.revision_id"
                >技能：{{ skill.name }}</ATag
              >
            </div>
          </div>
        </article>
        <article v-if="activeRun" class="message-row assistant">
          <AAvatar class="message-avatar" :size="30"><RobotOutlined /></AAvatar>
          <div class="message-body">
            <div class="message-meta">
              智衡 <ATag>{{ runStatusLabel[activeRun.status] }}</ATag>
            </div>
            <AiMessage
              :content="activeRun.text || (running ? '' : '本次运行未生成文本')"
              :reasoning-content="activeRun.reasoning ?? ''"
              :loading="running"
            />
            <p v-if="activeRun.error" class="run-error">{{ activeRun.error.message }}</p>
            <KnowledgeSources :sources="activeRunSources" />
            <span v-if="activeRun.usage.status === 'unknown'" class="muted small">用量待核对</span>
          </div>
        </article>
      </section>
      <footer class="composer">
        <div class="composer-context">
          <span class="model-capability">{{ supportsVision ? '文本与图片' : '文本对话' }}</span
          ><span v-if="!modelOptions.length && !listLoading" class="muted small"
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
        <div v-if="selectedKnowledgeBases.length" class="selected-skills">
          <ATag
            v-for="base in selectedKnowledgeBases"
            :key="base.id"
            :closable="!running && !sending"
            @close="
              selectedKnowledgeBases = selectedKnowledgeBases.filter((item) => item.id !== base.id)
            "
            >知识库：{{ base.name }}</ATag
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
            <SkillPicker
              v-model="selectedSkills"
              v-model:open="skillPickerOpen"
              :disabled="running || sending || loading"
              @selected="handleSkillSelected"
            />
            <KnowledgePicker
              v-model="selectedKnowledgeBases"
              :disabled="running || sending || loading || readOnly"
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
            }}</span
            ><AButton
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
                readOnly
              "
              aria-label="发送消息"
              @click="send()"
              ><SendOutlined />发送</AButton
            >
          </div>
        </div>
      </footer>
    </main>
  </div>
</template>
<style scoped lang="scss">
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
  height: 100%;
  display: flex;
  overflow: hidden;
  color: var(--color-text);
  background: var(--gray-10);
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

.chat-workspace {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 0;
  background: var(--gray-10);
}

.toolbar-icon-button {
  flex-shrink: 0;
  width: var(--workspace-control-height);
  height: var(--workspace-control-height);
}

.model-select {
  width: min(320px, 100%);
  min-width: 0;
}

.chat-toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-height: 52px;
  padding: 8px 24px;
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

.model-capability {
  flex-shrink: 0;
  padding: 3px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 20px;
}

.model-capability:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}

.model-capability-description {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.message-history {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 28px clamp(16px, 6vw, 96px);
  background: transparent;
}

.message-row {
  display: flex;
  gap: 12px;
  max-width: 960px;
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
  border-radius: 50%;
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
  text-align: right;
}

.user-message {
  margin: 5px 0 0;
  padding: 10px 13px;
  border: 1px solid var(--main-100);
  border-radius: 16px 4px 16px 16px;
  background: var(--main-50);
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
  padding: 12px 24px 24px;
  background: transparent;
}

.composer-surface {
  flex-shrink: 0;
  padding: 16px 18px 12px;
  border: 1px solid var(--gray-200);
  border-radius: 20px;
  background: var(--bg-container);
  box-shadow: 0 6px 24px rgb(0 0 0 / 4%);
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
  justify-content: flex-start;
  gap: 8px;
  margin-top: 8px;
}

.composer-actions :deep(.ant-upload-wrapper),
.composer-actions :deep(.ant-upload) {
  display: inline-flex;
}

.composer-submit {
  margin-left: auto;
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
.is-empty .chat-workspace {
  display: grid;
  grid-template-rows: auto minmax(min-content, 1fr) auto minmax(24px, 1fr);
  overflow-y: auto;
}
.is-empty .message-history {
  align-self: end;
  overflow: visible;
  padding: 28px 16px 0;
}
.is-empty .composer {
  width: min(800px, calc(100% - 48px));
  max-height: none;
  justify-self: center;
  align-self: start;
  padding: 24px 0 8px;
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
  .model-select {
    width: 180px;
  }
}

@media (max-width: 480px) {
  .model-select {
    width: 150px;
    min-width: 0;
  }
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
  .is-empty .composer {
    width: calc(100% - 28px);
    padding-top: 22px;
  }
}
</style>

<style scoped>
.chat-alert {
  padding: 0 24px;
}
.chat-alert :deep(.ant-alert) {
  margin-bottom: 8px;
}
.chat-alert :deep(.ant-btn) {
  margin: 0 8px 8px 0;
}
.platform-chat:has(.chat-alert).is-empty .chat-workspace {
  display: flex;
}
.platform-chat:has(.chat-alert).is-empty .message-history {
  align-self: stretch;
  flex: 1;
}
</style>
