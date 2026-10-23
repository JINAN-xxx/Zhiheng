<template>
  <div class="platform-chat workspace-chat-page" :class="{ 'is-empty': messages.length === 0 && !sessionId && !route.query.sessionId }">
    <main class="chat-workspace">
      <header class="chat-toolbar">
        <a-select
          v-model:value="modelId"
          class="model-select"
          :options="modelOptions"
          :filter-option="filterModelOption"
          show-search
          aria-label="当前对话模型"
          placeholder="选择平台模型"
          :disabled="loading || sessionLoading || isArchived"
        />
        <a-tooltip title="对话偏好">
          <a-button class="toolbar-icon-button" type="text" aria-label="对话偏好" @click="showPreferences = true">
            <template #icon><SettingOutlined /></template>
          </a-button>
        </a-tooltip>
      </header>
      <section ref="historyRef" class="message-history">
        <ChatEmptyState
          v-if="messages.length === 0 && !sessionId && !route.query.sessionId"
          :can-work="canWork"
          @mode="value => value === 'work' && router.push('/ai/chat/work')"
          @prompt="draft = $event"
        />
        <article v-for="message in messages" :key="message.localId || message.messageId" class="message-row" :class="message.role">
          <a-avatar class="message-avatar" :size="30">
            <UserOutlined v-if="message.role === 'user'" />
            <RobotOutlined v-else />
          </a-avatar>
          <div class="message-body">
            <div class="message-meta">{{ chatMessageLabel(message.role) }}</div>
            <div v-if="message.skillSnapshots?.length" class="message-skills">
              <a-tag v-for="skill in message.skillSnapshots" :key="skill.slug">技能：{{ skill.name }}</a-tag>
            </div>
            <div v-if="knowledgeNames(message).length" class="message-knowledge">
              <a-tag v-for="name in knowledgeNames(message)" :key="name">历史引用：{{ name }}</a-tag>
            </div>
            <ChatMessageFiles :images="message.images" :attachments="message.attachments" />
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

      <footer class="composer" :class="{ 'composer--archived': isArchived }">
        <div class="composer-context">
          <template v-if="uploadSettings.model">
            <a-tooltip :trigger="['hover', 'focus']" :title="imageHint || '当前模型支持图片理解，也可上传文档附件。'">
              <span class="model-capability" tabindex="0" :aria-describedby="capabilityDescriptionId">{{ imageHint ? '文本与文档' : '支持图片' }}</span>
            </a-tooltip>
            <span :id="capabilityDescriptionId" class="model-capability-description">{{ imageHint || '当前模型支持图片理解，也可上传文档附件。' }}</span>
          </template>
          <div class="composer-context-actions">
            <WorkspaceStatus v-if="stateLabel" :status="runState" :label="stateLabel" />
          </div>
        </div>
        <div v-if="isArchived" class="archived-actions" role="status">
          <div class="archived-message">
            <InboxOutlined />
            <span>此对话已归档，恢复后可继续发送消息。</span>
          </div>
          <a-button type="primary" aria-label="恢复对话" :loading="restoringSession" @click="restoreCurrentSession">
            <RollbackOutlined />恢复对话
          </a-button>
        </div>
        <template v-else>
          <div v-if="selectedSkills.length" class="selected-skills">
            <a-tag
              v-for="skill in selectedSkills"
              :key="skill.skillSlug"
              closable
              @close="removeSkill(skill.skillSlug)"
            >技能：{{ skill.skillName }}</a-tag>
          </div>
          <div v-if="fileItems.length" class="file-queue" aria-label="待发送文件">
            <div v-for="file in fileItems" :key="file.id" class="queued-file" :class="{ 'has-error': file.status === 'failed' }">
              <img v-if="file.previewUrl" :src="file.previewUrl" :alt="file.fileName" class="queued-file-preview" />
              <FileTextOutlined v-else class="queued-file-icon" />
              <div class="queued-file-details">
                <span class="queued-file-name" :title="file.fileName">{{ file.fileName }}</span>
                <span v-if="file.status === 'uploading'" class="queued-file-status" role="status">上传中 {{ file.progress }}%</span>
                <span v-else-if="file.status === 'failed'" class="queued-file-error" role="alert">{{ file.error }}</span>
                <span v-else class="queued-file-status">已上传</span>
              </div>
              <a-button v-if="file.status === 'failed'" type="link" size="small" :disabled="loading" :aria-label="`重试上传：${file.fileName}`" @click="fileQueue.retry(file.id)">重试</a-button>
              <a-button type="text" size="small" :disabled="loading" :aria-label="`移除文件：${file.fileName}`" @click="fileQueue.remove(file.id)"><CloseOutlined /></a-button>
            </div>
          </div>
          <div class="composer-surface">
            <a-textarea
              v-model:value="draft"
              :auto-size="{ minRows: 3, maxRows: 6 }"
              :disabled="loading || sessionLoading"
              placeholder="输入消息，按回车发送，按住上档键回车换行"
              @keydown.enter.exact.prevent="send"
            />
            <div class="composer-actions">
              <SkillPicker
                v-model="selectedSkills"
                v-model:open="skillPickerOpen"
                :disabled="loading"
                @selected="handleSkillSelected"
              />
              <input ref="fileInputRef" class="chat-file-input" type="file" :accept="CHAT_FILE_ACCEPT" multiple :disabled="loading || sessionLoading" tabindex="-1" aria-label="选择图片或附件" @change="selectFiles" />
              <a-tooltip title="添加图片或附件（PDF、DOCX、TXT、MD、XLSX、CSV）">
                <a-button type="text" :disabled="loading || sessionLoading" aria-label="添加图片或附件" @click="fileInputRef?.click()">
                  <template #icon><PaperClipOutlined /></template>
                </a-button>
              </a-tooltip>
              <span class="composer-hint">图片 {{ imageCount }}/10 · 文档 {{ attachmentCount }}/5</span>
              <a-button v-if="loading" class="composer-submit" danger @click="stop"><PauseCircleOutlined />停止</a-button>
              <a-button v-else class="composer-submit" type="primary" aria-label="发送" :disabled="!canSend" @click="send"><SendOutlined />发送</a-button>
            </div>
            <p v-if="imageCount && imageHint" class="file-guidance">{{ imageHint }}</p>
            <p v-if="filesFailed" class="file-guidance file-guidance--error" role="status">请重试或移除上传失败的文件后再发送。</p>
            <p v-else-if="fileItems.length" class="file-guidance">图片单张最多 {{ Math.min(50, preferences.imageMaxSizeMb || 10) }} MB，文档单个最多 20 MB。</p>
          </div>
        </template>
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
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, useId, watch } from "vue";
import { v4 as uuidv4 } from 'uuid';
import {
  InboxOutlined,
  CloseOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  PauseCircleOutlined,
  RobotOutlined,
  RollbackOutlined,
  SendOutlined,
  SettingOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { normalizeErrorMessage } from "@/utils/errorMessage";
import useWorkspaceStore from "@/store/modules/workspace";
import useUserStore from "@/store/modules/user";
import { listPlatformModels } from "../../api/modelCatalog";
import {
  cancelChatRun,
  createChatStream,
  getChatSession,
  getUserChatConfig,
  saveUserChatConfig,
  uploadChatFile,
} from "../../api/chat";
import AiMessage from "./components/AiMessage.vue";
import ChatMessageFiles from './components/ChatMessageFiles.vue'
import SkillPicker from './components/SkillPicker.vue'
import ChatEmptyState from './components/ChatEmptyState.vue'
import WorkspaceStatus from "@/components/Workspace/WorkspaceStatus.vue";
import { beginSend, chatMessageLabel, resolveChatModelOptions, legacyKnowledgeLabels } from "../../utils/chatTransforms.js";
import { removeTerminalSkillTrigger, shouldOpenSkillPicker } from '../../../agent_extension/public/skills.js'
import { CHAT_FILE_ACCEPT, chatFilePayload, chatImageHint, createChatFileQueue } from '../../utils/chatFiles.js'

const { proxy } = getCurrentInstance();
const capabilityDescriptionId = useId();
const route = useRoute();
const router = useRouter();
const workspaceStore = useWorkspaceStore();
const userStore = useUserStore();
const models = ref([]);
const modelId = ref("");
const sessionId = ref(null);
const sessionStatus = ref('active');
const sessionLoading = ref(false);
const isArchived = computed(() => sessionStatus.value === 'archived');
const restoringSession = computed(() => workspaceStore.sessionMutatingIds.includes(sessionId.value));
const messages = ref([]);
const draft = ref("");
const fileItems = ref([]);
const fileInputRef = ref(null);
const fileQueue = createChatFileQueue({ upload: uploadChatFile, onChange: items => { fileItems.value = items; } });
const selectedSkills = ref([])
const canWork = computed(() => (userStore.permissions || []).some(permission => ['*:*:*', 'agent_extension:work:list', 'agent_extension:work:query', 'agent_extension:work:add'].includes(permission)))
const skillPickerOpen = ref(false)
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

const stateLabel = computed(() => ({ connecting: "连接中", streaming: "生成中", completed: "已完成", cancelled: "已取消", interrupted: "已中断", failed: "失败" }[runState.value] || ""));
const modelOptions = computed(() => resolveChatModelOptions(models.value))
const uploadSettings = computed(() => ({ ...preferences, model: models.value.find(model => model.modelId === modelId.value) }))
const imageHint = computed(() => chatImageHint(uploadSettings.value))
const imageCount = computed(() => fileItems.value.filter(file => file.kind === 'image').length)
const attachmentCount = computed(() => fileItems.value.filter(file => file.kind === 'attachment').length)
const filesUploading = computed(() => fileItems.value.some(file => file.status === 'uploading'))
const filesFailed = computed(() => fileItems.value.some(file => file.status === 'failed'))
const canSend = computed(() => !loading.value && !sessionLoading.value && !isArchived.value && Boolean(modelId.value)
  && Boolean(draft.value.trim() || fileItems.value.length) && !filesUploading.value && !filesFailed.value
  && !(imageCount.value && imageHint.value))

function filterModelOption(input, option) {
  return String(option?.label ?? '').toLowerCase().includes(String(input).toLowerCase())
}

function knowledgeNames(message) { return legacyKnowledgeLabels(message) }

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
  resetFileDraft();
  const requestGeneration = ++sessionLoadGeneration;
  const statusChangeAtStart = workspaceStore.sessionStatusChanges[id];
  sessionLoading.value = true;
  try {
    const response = await getChatSession(id);
    if (requestGeneration !== sessionLoadGeneration) return;
    sessionId.value = id;
    const confirmedChange = workspaceStore.sessionStatusChanges[id];
    // A sidebar restore may complete while this older detail response is in flight.
    sessionStatus.value = confirmedChange && confirmedChange !== statusChangeAtStart
      ? confirmedChange.status
      : response.data?.session?.status || 'active';
    modelId.value = response.data?.session?.modelId || modelId.value;
    messages.value = (response.data?.messages || []).map((item) => ({ ...item, localId: item.messageId }));
    selectedSkills.value = []
    skillPickerOpen.value = false
    await scrollToBottom();
  } finally {
    if (requestGeneration === sessionLoadGeneration) sessionLoading.value = false;
  }
}

function newSession() {
  if (loading.value) return;
  resetFileDraft();
  sessionLoadGeneration += 1;
  sessionId.value = null;
  sessionStatus.value = 'active';
  sessionLoading.value = false;
  messages.value = [];
  selectedSkills.value = []
  skillPickerOpen.value = false
  runState.value = "idle";
}

async function restoreCurrentSession() {
  const id = sessionId.value;
  if (!id || restoringSession.value) return;
  try {
    if (!await workspaceStore.setSessionStatus(id, 'active')) return;
    await workspaceStore.loadSessions({ status: 'active', pageNum: 1 });
    proxy.$modal.msgSuccess('对话已恢复');
  } catch {
    // API errors are shown by the shared client; the archived view remains intact.
  }
}

function resetFileDraft() {
  fileQueue.clear();
  draft.value = '';
  if (fileInputRef.value) fileInputRef.value.value = '';
}

function selectFiles(event) {
  if (loading.value || sessionLoading.value || isArchived.value) return;
  const { rejected } = fileQueue.add(event.target.files, uploadSettings.value);
  event.target.value = '';
  for (const rejection of rejected) proxy.$modal.msgError(`${rejection.fileName}：${rejection.error}`);
}

function removeSkill(slug) {
  selectedSkills.value = selectedSkills.value.filter((skill) => skill.skillSlug !== slug)
}

function handleSkillSelected() {
  draft.value = removeTerminalSkillTrigger(draft.value)
}

async function send() {
  if (!canSend.value) return;
  const message = draft.value.trim() || '请分析上传的文件。';
  const requestGeneration = sessionLoadGeneration;
  const nextState = beginSend({ runId: runId.value, status: runState.value });
  runId.value = nextState.runId;
  const clientMessageId = uuidv4();
  const outgoingFiles = chatFilePayload(fileItems.value);
  const fileMetadata = kind => fileItems.value.filter(file => file.kind === kind).map(({ fileId, fileName, mimeType, sizeBytes }) => ({ fileId, fileName, mimeType, sizeBytes }));
  const outgoingSkills = selectedSkills.value.map((skill) => ({ ...skill }))
  const skillSnapshots = outgoingSkills.map((skill) => ({ slug: skill.skillSlug, name: skill.skillName }))
  const userId = `local-${clientMessageId}`;
  messages.value.push({ localId: userId, role: "user", content: message, images: fileMetadata('image'), attachments: fileMetadata('attachment'), skillSnapshots });
  skillPickerOpen.value = false
  loading.value = true;
  await nextTick();
  const assistantId = `assistant-${clientMessageId}`;
  messages.value.push({ localId: assistantId, role: "assistant", content: "", reasoningContent: "", status: "pending" });
  activeAssistantId.value = assistantId;
  runState.value = nextState.status;
  abortController.value = new AbortController();
  let requestAccepted = false;
  await scrollToBottom();
  try {
    const stream = await createChatStream(
      {
        sessionId: sessionId.value,
        modelId: modelId.value,
        clientMessageId,
        message,
        images: outgoingFiles.images,
        attachments: outgoingFiles.attachments,
        skillSlugs: outgoingSkills.map((skill) => skill.skillSlug),
      },
      abortController.value.signal,
    );
    if (requestGeneration !== sessionLoadGeneration) return;
    requestAccepted = true;
    draft.value = "";
    fileQueue.clear();
    selectedSkills.value = []
    runState.value = "streaming";
    for await (const event of stream) handleEvent(event, assistantId);
  } catch (error) {
    if (!requestAccepted) messages.value = messages.value.filter(item => ![userId, assistantId].includes(item.localId));
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

watch(() => workspaceStore.sessionChange, (change) => {
  if (change?.sessionId === sessionId.value) sessionStatus.value = change.status;
});

watch(draft, (value) => {
  if (shouldOpenSkillPicker(value) && !loading.value) skillPickerOpen.value = true
})

function cleanup() {
  sessionLoadGeneration += 1;
  abortController.value?.abort();
  resetFileDraft();
}
onBeforeUnmount(cleanup);
onDeactivated(cleanup);
</script>

<style scoped lang="scss">
.archived-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.archived-message { display: flex; align-items: center; gap: 8px; color: var(--color-text-secondary); font-size: 13px; }
.platform-chat {
  height: 100%;
  display: flex;
  overflow: hidden;
  color: var(--color-text);
  background: var(--gray-10);
  font-family: system-ui, "Noto Sans SC", "HarmonyOS Sans SC", "MiSans", "PingFang SC", "Microsoft YaHei", sans-serif;
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
  line-height: 1.6;
}

.message-skills,
.selected-skills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}

.message-row.user .message-skills { justify-content: flex-end; }

.selected-skills { flex-shrink: 0; margin: 0 0 8px; }

.chat-file-input { display: none; }
.file-queue { display: flex; flex-wrap: wrap; gap: 8px; min-height: 60px; margin-bottom: 10px; max-height: 220px; overflow-y: auto; }
.queued-file { display: flex; align-items: center; gap: 8px; min-width: 0; width: min(100%, 380px); padding: 8px; border: 1px solid var(--gray-200); border-radius: 10px; background: var(--bg-container); }
.queued-file.has-error { border-color: var(--ant-color-error, #ff4d4f); }
.queued-file-preview { width: 40px; height: 40px; object-fit: cover; border-radius: 6px; flex: 0 0 40px; }
.queued-file-icon { font-size: 24px; color: var(--color-text-secondary); }
.queued-file-details { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.queued-file-name { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queued-file-status, .queued-file-error { font-size: 11px; overflow-wrap: anywhere; }
.queued-file-status { color: var(--color-text-secondary); }
.queued-file-error, .file-guidance--error { color: var(--ant-color-error, #ff4d4f); }
.file-guidance { margin: 8px 4px 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.file-guidance--error { color: var(--ant-color-error, #ff4d4f); }
.message-row.user :deep(.message-files) { justify-content: flex-end; }

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
  transition: border-color .18s, box-shadow .18s;
}

.composer-surface:focus-within {
  border-color: var(--main-400);
  box-shadow: 0 0 0 3px var(--main-50), 0 6px 24px rgb(0 0 0 / 4%);
}

.composer-surface :deep(.ant-input) {
  padding: 4px;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.composer :deep(textarea) {
  line-height: 1.6;
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

.model-select :deep(.ant-select-selector) { border-color: transparent; border-radius: 10px; background: transparent; box-shadow: none; }
.composer-actions :deep(.ant-btn-text) { border-radius: 9px; color: var(--color-text-secondary); }
.is-empty .chat-workspace { display: grid; grid-template-rows: auto minmax(min-content, 1fr) auto minmax(24px, 1fr); overflow-y: auto; }
.is-empty .message-history { align-self: end; overflow: visible; padding: 28px 16px 0; }
.is-empty .composer { width: min(800px, calc(100% - 48px)); max-height: none; justify-self: center; align-self: start; padding: 24px 0 8px; }

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

  .message-history { padding: 16px 12px; }
  .model-select { width: 180px; }
}

@media (max-width: 480px) {
  .model-select { width: 150px; min-width: 0; }
  .composer-surface { padding: 12px; border-radius: 16px; }
  .composer-actions { gap: 5px; }
  .composer-submit { width: 76px; }
  .is-empty .composer { width: calc(100% - 28px); padding-top: 22px; }
}
</style>
