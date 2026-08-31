<template>
  <div class="platform-chat workspace-chat-page">
    <main class="chat-workspace">
      <header class="chat-toolbar">
        <div>
          <WorkspaceStatus :status="runState" :label="stateLabel" />
        </div>
        <div class="toolbar-actions">
          <a-select
            v-model:value="modelId"
            class="model-select"
            :options="modelOptions"
            :filter-option="filterModelOption"
            show-search
            placeholder="选择平台模型"
            :disabled="loading"
          />
          <a-tooltip title="对话偏好">
            <a-button class="toolbar-icon-button" type="text" aria-label="对话偏好" @click="showPreferences = true">
              <template #icon><SettingOutlined /></template>
            </a-button>
          </a-tooltip>
        </div>
      </header>

      <section ref="historyRef" class="message-history">
        <div v-if="messages.length === 0" class="empty-chat">
          <MessageOutlined class="empty-chat__icon" />
          <h2>从一个问题开始</h2>
          <p>选择一个平台模型，开始你的工作流。</p>
        </div>
        <article v-for="message in messages" :key="message.localId || message.messageId" class="message-row" :class="message.role">
          <a-avatar class="message-avatar" :size="30">
            <UserOutlined v-if="message.role === 'user'" />
            <RobotOutlined v-else />
          </a-avatar>
          <div class="message-body">
            <div class="message-meta">{{ chatMessageLabel(message.role) }}</div>
            <div v-if="message.images?.length" class="message-images">
              <a-tag v-for="image in message.images" :key="image.fileId">文件 {{ image.fileId }}</a-tag>
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
          <a-tag v-for="fileId in imageIds" :key="fileId" closable @close="removeImage(fileId)">文件 {{ fileId }}</a-tag>
        </div>
        <div class="composer-surface">
          <a-textarea
            v-model:value="draft"
            :auto-size="{ minRows: 3, maxRows: 6 }"
            :disabled="loading"
            placeholder="输入消息，按回车发送，按住上档键回车换行"
            @keydown.enter.exact.prevent="send"
          />
          <div class="composer-actions">
            <a-upload
              accept="image/png,image/jpeg,image/webp,image/gif"
              :before-upload="selectImage"
              :disabled="loading"
              :show-upload-list="false"
            >
              <a-tooltip title="添加图片">
                <a-button type="text" :disabled="loading" aria-label="添加图片">
                  <template #icon><PictureOutlined /></template>
                </a-button>
              </a-tooltip>
            </a-upload>
            <span class="composer-hint">{{ imageIds.length }}/10</span>
            <a-button v-if="loading" class="composer-submit" danger @click="stop"><PauseCircleOutlined />停止</a-button>
            <a-button v-else class="composer-submit" type="primary" :disabled="!draft.trim() || !modelId" @click="send"><SendOutlined />发送</a-button>
          </div>
        </div>
      </footer>
    </main>

    <a-modal v-model:open="showPreferences" title="对话偏好" :width="520">
      <a-form :label-col="{ span: 8 }" :wrapper-col="{ span: 16 }">
        <a-form-item label="温度"><a-input-number v-model:value="editingPreferences.temperature" :min="0" :max="2" :step="0.1" /></a-form-item>
        <a-form-item label="附带历史"><a-switch v-model:checked="editingPreferences.addHistory" /></a-form-item>
        <a-form-item label="历史消息数"><a-input-number v-model:value="editingPreferences.historyMessageCount" :min="0" :max="100" /></a-form-item>
        <a-form-item label="系统提示词"><a-textarea v-model:value="editingPreferences.systemPrompt" :rows="4" /></a-form-item>
        <a-form-item label="展示用量"><a-switch v-model:checked="editingPreferences.metricsVisible" /></a-form-item>
        <a-form-item label="启用视觉"><a-switch v-model:checked="editingPreferences.visionEnabled" /></a-form-item>
        <a-form-item label="图片上限(MB)"><a-input-number v-model:value="editingPreferences.imageMaxSizeMb" :min="1" :max="50" /></a-form-item>
      </a-form>
      <template #footer>
        <a-button @click="showPreferences = false">取消</a-button>
        <a-button type="primary" @click="savePreferences">保存</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup name="AiChat">
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import {
  MessageOutlined,
  PauseCircleOutlined,
  PictureOutlined,
  RobotOutlined,
  SendOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { getToken } from "@/utils/auth";
import { normalizeErrorMessage } from "@/utils/errorMessage";
import useWorkspaceStore from "@/store/modules/workspace";
import { listPlatformModels } from "../../api/modelCatalog";
import {
  cancelChatRun,
  createChatStream,
  getChatSession,
  getUserChatConfig,
  saveUserChatConfig,
} from "../../api/chat";
import AiMessage from "./components/AiMessage.vue";
import WorkspaceStatus from "@/components/Workspace/WorkspaceStatus.vue";
import { beginSend, chatMessageLabel, resolveChatModelOptions } from "../../utils/chatTransforms.js";

const { proxy } = getCurrentInstance();
const route = useRoute();
const router = useRouter();
const workspaceStore = useWorkspaceStore();
const models = ref([]);
const modelId = ref("");
const sessionId = ref(null);
const messages = ref([]);
const draft = ref("");
const imageIds = ref([]);
const loading = ref(false);
const runState = ref("idle");
const runId = ref(null);
const activeAssistantId = ref(null);
const historyRef = ref(null);
const abortController = ref(null);
const showPreferences = ref(false);
let sessionLoadGeneration = 0;
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

const stateLabel = computed(() => ({ idle: "就绪", connecting: "连接中", streaming: "生成中", completed: "已完成", cancelled: "已取消", interrupted: "已中断", failed: "失败" }[runState.value] || "就绪"));
const modelOptions = computed(() => resolveChatModelOptions(models.value))

function filterModelOption(input, option) {
  return String(option?.label ?? '').toLowerCase().includes(String(input).toLowerCase())
}

async function loadModels() {
  const response = await listPlatformModels({ pageNum: 1, pageSize: 100 });
  models.value = response.rows || response.data?.rows || [];
  const chatModels = models.value.filter((model) => model?.modelType === "chat");
  if (!chatModels.some((model) => model.modelId === modelId.value)) modelId.value = chatModels[0]?.modelId || "";
}

async function loadPreferences() {
  const response = await getUserChatConfig();
  Object.assign(preferences, response.data || defaultPreferences());
  Object.assign(editingPreferences, preferences);
}

async function openSession(id) {
  if (loading.value || id === sessionId.value) return;
  const requestGeneration = ++sessionLoadGeneration;
  const response = await getChatSession(id);
  if (requestGeneration !== sessionLoadGeneration) return;
  sessionId.value = id;
  messages.value = (response.data?.messages || []).map((item) => ({ ...item, localId: item.messageId }));
  await scrollToBottom();
}

function newSession() {
  if (loading.value) return;
  sessionLoadGeneration += 1;
  sessionId.value = null;
  messages.value = [];
  runState.value = "idle";
}

async function selectImage(file) {
  if (!file || imageIds.value.length >= 10) return false;
  if (file.size > (preferences.imageMaxSizeMb || 10) * 1024 * 1024) {
    proxy.$modal.msgError("图片超过当前偏好设置的大小上限");
    return false;
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
    proxy.$modal.msgError(normalizeErrorMessage(error, "图片上传失败"));
  }
  return false
}

function removeImage(id) {
  imageIds.value = imageIds.value.filter((item) => item !== id);
}

async function send() {
  const message = draft.value.trim();
  if (!message || !modelId.value || loading.value) return;
  const nextState = beginSend({ runId: runId.value, status: runState.value });
  runId.value = nextState.runId;
  const clientMessageId = crypto.randomUUID();
  const outgoingImages = imageIds.value.map((fileId) => ({ fileId }));
  messages.value.push({ localId: `local-${clientMessageId}`, role: "user", content: message, images: outgoingImages });
  draft.value = "";
  imageIds.value = [];
  await nextTick();
  const assistantId = `assistant-${clientMessageId}`;
  messages.value.push({ localId: assistantId, role: "assistant", content: "", reasoningContent: "", status: "pending" });
  activeAssistantId.value = assistantId;
  runState.value = nextState.status;
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
      proxy.$modal.msgError(normalizeErrorMessage(error, "对话请求失败"));
    }
  } finally {
    loading.value = false;
    abortController.value = null;
    await workspaceStore.loadSessions();
  }
}

function handleEvent(event, assistantId) {
  const message = messages.value.find((item) => item.localId === assistantId);
  if (!message) return;
  if (event.event === "meta") {
    const nextSessionId = event.data.sessionId || sessionId.value;
    if (nextSessionId && nextSessionId !== sessionId.value) {
      sessionId.value = nextSessionId;
      router.replace({ path: "/ai/chat", query: { sessionId: nextSessionId } });
    }
    runId.value = event.data.runId || null;
  } else if (event.event === "content") {
    message.content += event.data.delta || "";
  } else if (event.event === "reasoning") {
    message.reasoningContent += event.data.delta || "";
  } else if (event.event === "usage") {
    message.metrics = event.data;
  } else if (event.event === "error") {
    runState.value = "failed";
    proxy.$modal.msgError(normalizeErrorMessage(event.data, "模型运行失败"));
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
    await Promise.all([loadModels(), workspaceStore.loadSessions(), loadPreferences()]);
    const routedSessionId = String(route.query.sessionId || "");
    if (routedSessionId) await openSession(routedSessionId);
    else newSession();
  } catch (error) {
    proxy.$modal.msgError(normalizeErrorMessage(error, "平台对话初始化失败"));
  }
});

watch(() => route.query.sessionId, async (value) => {
  const routedSessionId = String(value || "");
  if (!routedSessionId) {
    newSession();
    return;
  }
  if (routedSessionId !== sessionId.value) await openSession(routedSessionId);
});

onBeforeUnmount(() => {
  sessionLoadGeneration += 1;
  abortController.value?.abort();
});
</script>

<style scoped lang="scss">
.platform-chat {
  height: 100%;
  display: flex;
  overflow: hidden;
  color: var(--color-text);
  background: var(--gray-50);
  font-family: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", "MiSans", "PingFang SC", "Microsoft YaHei", sans-serif;
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

.toolbar-title {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toolbar-icon-button {
  width: 32px;
  height: 32px;
}

.model-select {
  width: 220px;
}

.chat-toolbar {
  min-height: 64px;
  padding: 0 28px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--bg-container);
}

.toolbar-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.message-history {
  flex: 1;
  overflow: auto;
  padding: 28px clamp(16px, 6vw, 96px);
  background: var(--bg-container);
}

.empty-chat {
  min-height: 100%;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 8px;
  color: var(--color-text-secondary);
}

.empty-chat__icon {
  color: var(--color-text-tertiary);
  font-size: 42px;
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
  color: var(--main-color);
  background: var(--gray-150);
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
  border-radius: 8px;
  background: var(--main-color);
  color: var(--gray-0);
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
  padding: 12px clamp(16px, 6vw, 96px) 16px;
  border-top: 1px solid var(--gray-150);
  background: var(--bg-container);
}

.composer-surface {
  padding: 10px 12px 8px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--bg-container);
}

.composer-surface:focus-within {
  border-color: var(--main-color);
}

.composer-surface :deep(.ant-input) {
  padding: 4px;
  border: 0;
  background: transparent;
}

.composer :deep(textarea) {
  line-height: 1.6;
}

.composer-actions {
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.composer-actions :deep(.ant-upload-wrapper),
.composer-actions :deep(.ant-upload) {
  display: inline-flex;
}

.composer-submit {
  width: 84px;
  height: 36px;
}

:deep(.ant-form-item .ant-input-number) {
  width: 100%;
}

@media (max-width: 760px) {
  .chat-workspace { padding: 0; }
  .chat-toolbar,
  .composer {
    padding-right: 14px;
    padding-left: 14px;
  }

  .message-history {
    padding: 16px 12px;
  }

  .model-select {
    width: 150px;
  }
}

@media (max-width: 480px) {
  .toolbar-actions {
    width: 100%;
  }

  .model-select {
    min-width: 0;
    width: auto;
    flex: 1;
  }
}
</style>
