<template>
  <div class="platform-chat workspace-chat-page">
    <aside class="chat-sessions">
      <ChatSessionPanel
        v-model:query="sessionQuery"
        :sessions="filteredSessions"
        :active-session-id="sessionId"
        :loading="sessionLoading"
        @new="newSession"
        @open="openSession"
        @archive="archiveSession"
      />
    </aside>

    <main class="chat-workspace">
      <header class="chat-toolbar">
        <el-button class="mobile-session-toggle" text :icon="Menu" aria-label="打开会话列表" title="打开会话列表" @click="mobileSessionsOpen = true" />
        <div>
          <div class="toolbar-title">
            <h1>AI 对话</h1>
            <WorkspaceStatus :status="runState" :label="stateLabel" />
          </div>
        </div>
        <div class="toolbar-actions">
          <el-select v-model="modelId" placeholder="选择平台模型" filterable :disabled="loading">
            <el-option v-for="model in models" :key="model.modelId" :label="model.title || model.modelCode" :value="model.modelId" />
          </el-select>
          <el-button class="toolbar-icon-button" text :icon="Setting" aria-label="对话偏好" title="对话偏好" @click="showPreferences = true" />
        </div>
      </header>

      <section ref="historyRef" class="message-history">
        <div v-if="messages.length === 0" class="empty-chat">
          <el-icon :size="42"><ChatDotRound /></el-icon>
          <h2>从一个问题开始</h2>
          <p>选择一个平台模型，开始你的工作流。</p>
        </div>
        <article v-for="message in messages" :key="message.localId || message.messageId" class="message-row" :class="message.role">
          <div class="message-avatar"><el-icon><User v-if="message.role === 'user'" /><Service v-else /></el-icon></div>
          <div class="message-body">
            <div class="message-meta">{{ chatMessageLabel(message.role) }}</div>
            <div v-if="message.images?.length" class="message-images">
              <el-tag v-for="image in message.images" :key="image.fileId" size="small" effect="plain">文件 {{ image.fileId }}</el-tag>
            </div>
            <AiMessage
              v-if="message.role === 'assistant'"
              :content="message.content"
              :reasoning-content="message.reasoningContent"
              :loading="loading && message.localId === activeAssistantId"
            />
            <p v-else class="user-message">{{ message.content }}</p>
            <div v-if="message.metrics" class="message-metrics">
              输入 {{ message.metrics.inputTokens ?? 0 }} · 输出 {{ message.metrics.outputTokens ?? 0 }}
            </div>
          </div>
        </article>
      </section>

      <footer class="composer">
        <div v-if="imageIds.length" class="image-queue">
          <el-tag v-for="fileId in imageIds" :key="fileId" closable @close="removeImage(fileId)">文件 {{ fileId }}</el-tag>
        </div>
        <div class="composer-surface">
          <el-input
            v-model="draft"
            type="textarea"
            :rows="3"
            resize="none"
            :disabled="loading"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="send"
          />
          <div class="composer-actions">
            <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden @change="selectImage" />
            <el-button text :icon="Picture" :disabled="loading" aria-label="添加图片" title="添加图片" @click="fileInput?.click()" />
            <span class="composer-hint">{{ imageIds.length }}/10</span>
            <el-button v-if="loading" type="danger" :icon="VideoPause" @click="stop">停止</el-button>
            <el-button v-else type="primary" :icon="Promotion" :disabled="!draft.trim() || !modelId" @click="send">发送</el-button>
          </div>
        </div>
      </footer>
    </main>

    <el-drawer v-model="mobileSessionsOpen" title="会话" direction="ltr" size="min(88vw, 320px)" class="mobile-session-drawer">
      <ChatSessionPanel
        v-model:query="sessionQuery"
        :sessions="filteredSessions"
        :active-session-id="sessionId"
        :loading="sessionLoading"
        @new="newSessionFromMobile"
        @open="openSessionFromMobile"
        @archive="archiveSession"
      />
    </el-drawer>

    <el-dialog v-model="showPreferences" title="对话偏好" width="520px">
      <el-form label-width="130px">
        <el-form-item label="温度"><el-input-number v-model="editingPreferences.temperature" :min="0" :max="2" :step="0.1" /></el-form-item>
        <el-form-item label="附带历史"><el-switch v-model="editingPreferences.addHistory" /></el-form-item>
        <el-form-item label="历史消息数"><el-input-number v-model="editingPreferences.historyMessageCount" :min="0" :max="100" /></el-form-item>
        <el-form-item label="系统提示词"><el-input v-model="editingPreferences.systemPrompt" type="textarea" :rows="4" /></el-form-item>
        <el-form-item label="展示用量"><el-switch v-model="editingPreferences.metricsVisible" /></el-form-item>
        <el-form-item label="启用视觉"><el-switch v-model="editingPreferences.visionEnabled" /></el-form-item>
        <el-form-item label="图片上限(MB)"><el-input-number v-model="editingPreferences.imageMaxSizeMb" :min="1" :max="50" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPreferences = false">取消</el-button>
        <el-button type="primary" @click="savePreferences">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup name="AiChat">
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ChatDotRound, Menu, Picture, Promotion, Service, Setting, User, VideoPause } from "@element-plus/icons-vue";
import { getToken } from "@/utils/auth";
import { listPlatformModels } from "../../api/modelCatalog";
import {
  cancelChatRun,
  createChatStream,
  delChatSession,
  getChatSession,
  getUserChatConfig,
  listChatSession,
  saveUserChatConfig,
} from "../../api/chat";
import AiMessage from "./components/AiMessage.vue";
import ChatSessionPanel from "./components/ChatSessionPanel.vue";
import WorkspaceStatus from "@/components/Workspace/WorkspaceStatus.vue";
import { chatMessageLabel, normalizeChatSessions, resolveInitialSessionId } from "../../utils/chatTransforms.js";

const { proxy } = getCurrentInstance();
const models = ref([]);
const modelId = ref("");
const sessions = ref([]);
const sessionQuery = ref("");
const sessionId = ref(null);
const messages = ref([]);
const draft = ref("");
const imageIds = ref([]);
const loading = ref(false);
const sessionLoading = ref(false);
const runState = ref("idle");
const runId = ref(null);
const activeAssistantId = ref(null);
const historyRef = ref(null);
const fileInput = ref(null);
const abortController = ref(null);
const showPreferences = ref(false);
const mobileSessionsOpen = ref(false);
const preferences = reactive(defaultPreferences());
const editingPreferences = reactive(defaultPreferences());

function defaultPreferences() {
  return {
    temperature: null,
    addHistory: true,
    historyMessageCount: 20,
    systemPrompt: null,
    metricsVisible: true,
    visionEnabled: true,
    imageMaxSizeMb: 10,
  };
}

const stateLabel = computed(() => ({ idle: "就绪", loading: "准备中", streaming: "生成中", completed: "已完成", cancelled: "已取消", interrupted: "已中断", failed: "失败" }[runState.value] || "就绪"));
const filteredSessions = computed(() => {
  const query = sessionQuery.value.trim().toLowerCase();
  if (!query) return sessions.value;
  return sessions.value.filter((session) => session.title.toLowerCase().includes(query));
});

async function loadModels() {
  const response = await listPlatformModels({ pageNum: 1, pageSize: 100 });
  models.value = response.rows || response.data?.rows || [];
  if (!modelId.value && models.value.length) modelId.value = models.value[0].modelId;
}

async function loadSessions() {
  sessionLoading.value = true;
  try {
    const response = await listChatSession({ pageNum: 1, pageSize: 100 });
    sessions.value = normalizeChatSessions(response);
    const initialSessionId = resolveInitialSessionId(sessions.value, sessionId.value);
    if (!sessionId.value && initialSessionId) await openSession(initialSessionId);
  } finally {
    sessionLoading.value = false;
  }
}

async function loadPreferences() {
  const response = await getUserChatConfig();
  Object.assign(preferences, response.data || defaultPreferences());
  Object.assign(editingPreferences, preferences);
}

async function openSession(id) {
  if (loading.value || id === sessionId.value) return;
  const response = await getChatSession(id);
  sessionId.value = id;
  messages.value = (response.data?.messages || []).map((item) => ({ ...item, localId: item.messageId }));
  await scrollToBottom();
}

function newSession() {
  if (loading.value) return;
  sessionId.value = null;
  messages.value = [];
  runState.value = "idle";
}

function newSessionFromMobile() {
  mobileSessionsOpen.value = false;
  newSession();
}

async function openSessionFromMobile(id) {
  mobileSessionsOpen.value = false;
  await openSession(id);
}

async function archiveSession(id) {
  await proxy.$modal.confirm("是否归档该会话？");
  await delChatSession(id);
  if (id === sessionId.value) newSession();
  await loadSessions();
}

async function selectImage(event) {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file || imageIds.value.length >= 10) return;
  if (file.size > (preferences.imageMaxSizeMb || 10) * 1024 * 1024) {
    proxy.$modal.msgError("图片超过当前偏好设置的大小上限");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch(import.meta.env.VITE_APP_BASE_API + "/common/upload", {
      method: "POST",
      headers: { Authorization: "Bearer " + getToken() },
      body: form,
    });
    const payload = await response.json();
    const id = payload?.data?.fileId || payload?.fileId;
    if (!response.ok || !id) throw new Error("图片服务未返回平台文件ID");
    imageIds.value.push(id);
  } catch (error) {
    proxy.$modal.msgError(error?.message || "图片上传失败");
  }
}

function removeImage(id) {
  imageIds.value = imageIds.value.filter((item) => item !== id);
}

async function send() {
  const message = draft.value.trim();
  if (!message || !modelId.value || loading.value) return;
  runId.value = null;
  const clientMessageId = crypto.randomUUID();
  const outgoingImages = imageIds.value.map((fileId) => ({ fileId }));
  messages.value.push({ localId: `local-${clientMessageId}`, role: "user", content: message, images: outgoingImages });
  const assistantId = `assistant-${clientMessageId}`;
  messages.value.push({ localId: assistantId, role: "assistant", content: "", reasoningContent: "", status: "pending" });
  draft.value = "";
  imageIds.value = [];
  activeAssistantId.value = assistantId;
  runState.value = "loading";
  loading.value = true;
  abortController.value = new AbortController();
  await scrollToBottom();
  try {
    const stream = await createChatStream(
      { sessionId: sessionId.value, modelId: modelId.value, clientMessageId, message, images: outgoingImages },
      abortController.value.signal,
    );
    runState.value = "streaming";
    for await (const event of stream) handleEvent(event, assistantId);
  } catch (error) {
    if (error?.name !== "AbortError") {
      runState.value = "failed";
      proxy.$modal.msgError(error?.message || "对话请求失败");
    }
  } finally {
    loading.value = false;
    abortController.value = null;
    await loadSessions();
  }
}

function handleEvent(event, assistantId) {
  const message = messages.value.find((item) => item.localId === assistantId);
  if (!message) return;
  if (event.event === "meta") {
    sessionId.value = event.data.sessionId || sessionId.value;
    runId.value = event.data.runId || null;
  } else if (event.event === "content") {
    message.content += event.data.delta || "";
  } else if (event.event === "reasoning") {
    message.reasoningContent += event.data.delta || "";
  } else if (event.event === "usage") {
    message.metrics = event.data;
  } else if (event.event === "error") {
    runState.value = "failed";
    proxy.$modal.msgError(event.data.message || "模型运行失败");
  } else if (event.event === "done") {
    runState.value = event.data.status || "completed";
    message.status = runState.value;
  }
  scrollToBottom();
}

async function stop() {
  const controller = abortController.value;
  if (!controller) return;
  runState.value = "cancelled";
  if (runId.value) {
    try {
      await cancelChatRun(runId.value);
    } catch {
      // The Fetch abort still prevents the UI from waiting on a broken connection.
    }
  }
  controller.abort();
}

async function savePreferences() {
  const response = await saveUserChatConfig({ ...editingPreferences });
  Object.assign(preferences, response.data || editingPreferences);
  Object.assign(editingPreferences, preferences);
  showPreferences.value = false;
}

async function scrollToBottom() {
  await nextTick();
  if (historyRef.value) historyRef.value.scrollTop = historyRef.value.scrollHeight;
}

onMounted(async () => {
  try {
    await Promise.all([loadModels(), loadSessions(), loadPreferences()]);
  } catch (error) {
    proxy.$modal.msgError(error?.message || "平台对话初始化失败");
  }
});

onBeforeUnmount(() => abortController.value?.abort());
</script>

<style scoped lang="scss">
.platform-chat {
  height: calc(100vh - 116px);
  display: flex;
  overflow: hidden;
  color: var(--el-text-color-primary);
  background: var(--workspace-bg);
  font-family: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", "MiSans", "PingFang SC", "Microsoft YaHei", sans-serif;
}

.chat-sessions {
  width: 256px;
  flex: 0 0 256px;
  display: flex;
  flex-direction: column;
  margin: var(--workspace-space-4) 0 var(--workspace-space-4) var(--workspace-space-4);
  border: 1px solid var(--workspace-border);
  border-radius: var(--workspace-radius-md);
  background: var(--workspace-surface);
}

.chat-toolbar,
.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.run-state,
.message-meta,
.message-metrics,
.composer-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.chat-workspace {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.toolbar-title {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toolbar-icon-button {
  width: 32px;
  height: 32px;
}

.mobile-session-toggle {
  display: none;
}

:global(.mobile-session-drawer .el-drawer__body) {
  padding: 0;
}

.chat-toolbar {
  min-height: 60px;
  padding: 0 22px;
  border-bottom: 1px solid var(--el-border-color);
  background: transparent;
}

.chat-toolbar h1 {
  display: inline;
  margin: 0 12px 0 0;
  font-size: 17px;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.message-history {
  flex: 1;
  overflow: auto;
  padding: 28px clamp(16px, 7vw, 112px);
}

.empty-chat {
  min-height: 100%;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
}

.empty-chat h2,
.empty-chat p {
  margin: 0;
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
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.message-body {
  min-width: 0;
  max-width: min(760px, 90%);
}

.message-row.user .message-body {
  text-align: right;
}

.user-message {
  margin: 5px 0 0;
  padding: 10px 13px;
  border-radius: 7px;
  background: var(--el-color-primary-light-9);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.6;
}

.message-images,
.image-queue {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}

.message-metrics {
  margin-top: 6px;
}

.composer {
  padding: 12px clamp(16px, 7vw, 112px) 20px;
  background: transparent;
}

.composer-surface {
  padding: 10px 12px 8px;
  border: 1px solid var(--workspace-border-strong);
  border-radius: var(--workspace-radius-md);
  background: var(--workspace-surface);
  box-shadow: var(--workspace-shadow-soft);
}

.composer-surface :deep(.el-textarea__inner) {
  padding: 4px;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.composer :deep(textarea) {
  line-height: 1.6;
}

.composer-actions {
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

@media (max-width: 760px) {
  .chat-sessions {
    display: none;
  }

  .mobile-session-toggle {
    display: inline-flex;
    flex: 0 0 auto;
  }

  .chat-toolbar,
  .composer {
    padding-left: 12px;
    padding-right: 12px;
  }

  .message-history {
    padding: 16px 12px;
  }

  .toolbar-actions :deep(.el-select) {
    width: 150px;
  }
}
</style>
