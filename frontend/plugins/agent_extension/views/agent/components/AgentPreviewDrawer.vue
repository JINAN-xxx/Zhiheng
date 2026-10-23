<template>
  <a-drawer :open="modelValue" width="min(680px, 100vw)" :body-style="{ display: 'flex', flexDirection: 'column', padding: '0', minHeight: '0' }" @close="close">
    <template #title><span class="agent-drawer-title">{{ agent?.name || '智能体' }} · 试聊</span></template>
    <template #extra><a-tooltip title="清空对话"><a-button type="text" aria-label="清空对话" :disabled="!messages.length && !error" @click="reset"><template #icon><ClearOutlined /></template></a-button></a-tooltip></template>
    <div class="preview-version">已保存草稿 v{{ agent?.version }}</div>
    <div ref="historyRef" class="preview-history" aria-live="polite" :aria-busy="sending">
      <article v-if="welcomeMessage" class="preview-welcome preview-message preview-message--assistant">
        <div class="preview-message__label"><RobotOutlined /><strong>{{ agent?.name }}</strong></div>
        <div class="preview-message__content">{{ welcomeMessage }}</div>
      </article>
      <WorkspaceState v-else-if="!messages.length && !sending" kind="empty" title="暂无消息" />
      <article v-for="(item, index) in messages" :key="index" class="preview-message" :class="`preview-message--${item.role}`">
        <div class="preview-message__label"><UserOutlined v-if="item.role === 'user'" /><RobotOutlined v-else /><strong>{{ item.role === 'user' ? '我' : agent?.name }}</strong></div>
        <div class="preview-message__content">{{ item.content }}</div>
      </article>
      <div v-if="sending" class="preview-pending"><a-spin size="small" /><span>正在生成</span></div>
      <a-alert v-if="error" class="preview-error" type="error" :message="error" show-icon />
      <a-collapse v-if="completion" class="preview-json" ghost><a-collapse-panel key="json" header="JSON 响应"><pre>{{ JSON.stringify(completion, null, 2) }}</pre></a-collapse-panel></a-collapse>
    </div>
    <div class="preview-composer">
      <a-textarea v-model:value="draft" aria-label="试聊消息" placeholder="输入消息" :auto-size="{ minRows: 3, maxRows: 7 }" :maxlength="32000" :disabled="sending" @keydown.enter.exact.prevent="send" />
      <div class="preview-composer__actions">
        <a-tooltip v-if="sending" title="停止生成"><a-button aria-label="停止生成" @click="cancel"><template #icon><StopOutlined /></template></a-button></a-tooltip>
        <a-button v-else type="primary" :disabled="!draft.trim() || !agent?.agentId" @click="send"><template #icon><SendOutlined /></template>发送</a-button>
      </div>
    </div>
  </a-drawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ClearOutlined, RobotOutlined, SendOutlined, StopOutlined, UserOutlined } from '@ant-design/icons-vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import { previewAgent } from '../../../api/agent.js'
import { buildAgentPreviewPayload, extractAgentCompletion } from '../../../utils/agent.js'

const props = defineProps({ modelValue: Boolean, agent: { type: Object, default: null } })
const emit = defineEmits(['update:modelValue'])
const welcomeMessage = computed(() => String(props.agent?.welcomeMessage || '').trim())
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const error = ref('')
const completion = ref(null)
const historyRef = ref()
let generation = 0
let controller
let pendingText = ''

async function scroll() { await nextTick(); historyRef.value?.scrollTo({ top: historyRef.value.scrollHeight, behavior: 'smooth' }) }

async function send(event) {
  if (event?.isComposing || sending.value || !draft.value.trim() || !props.agent?.agentId) return
  const requestId = ++generation
  const content = draft.value.trim()
  const conversation = [...messages.value, { role: 'user', content }]
  messages.value = conversation
  pendingText = content
  draft.value = ''; error.value = ''; completion.value = null; sending.value = true
  controller = new AbortController()
  scroll()
  try {
    const response = await previewAgent(props.agent.agentId, buildAgentPreviewPayload(conversation), controller.signal)
    if (requestId !== generation) return
    messages.value = [...conversation, { role: 'assistant', content: extractAgentCompletion(response) }]
    completion.value = response.data ?? response
    pendingText = ''
  } catch (reason) {
    if (requestId !== generation) return
    messages.value = conversation.slice(0, -1)
    draft.value = content
    pendingText = ''
    error.value = normalizeErrorMessage(reason, '试聊失败')
  } finally {
    if (requestId === generation) { sending.value = false; controller = null; scroll() }
  }
}

function cancel() {
  generation += 1
  controller?.abort()
  controller = null
  if (sending.value && pendingText) { messages.value = messages.value.slice(0, -1); draft.value = pendingText }
  pendingText = ''; sending.value = false
}

function reset() { cancel(); messages.value = []; draft.value = ''; error.value = ''; completion.value = null }
function close() { reset(); emit('update:modelValue', false) }
watch(() => [props.modelValue, props.agent?.agentId, props.agent?.version], reset)
onBeforeUnmount(cancel)
</script>

<style scoped>
.agent-drawer-title { display: block; overflow-wrap: anywhere; }
.preview-version { padding: 10px 24px; border-bottom: 1px solid var(--gray-150); color: var(--color-text-secondary); font-size: 12px; }
.preview-history { flex: 1; min-height: 0; overflow-y: auto; padding: 20px 24px; }
.preview-message { padding-bottom: 24px; }
.preview-message__label { display: flex; align-items: center; gap: 8px; margin-bottom: 9px; color: var(--color-text-secondary); font-size: 13px; }
.preview-message__label strong { min-width: 0; overflow-wrap: anywhere; }
.preview-message--assistant .preview-message__label { color: var(--main-700); }
.preview-message__content { padding-left: 22px; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--color-text); font-size: 14px; line-height: 1.75; }
.preview-pending { display: flex; align-items: center; gap: 10px; padding-bottom: 20px; color: var(--color-text-secondary); font-size: 13px; }
.preview-error { margin-bottom: 16px; }
.preview-json pre { max-height: 250px; overflow: auto; padding: 12px; background: var(--gray-50); border: 1px solid var(--gray-150); border-radius: 6px; font-size: 12px; }
.preview-composer { flex: 0 0 auto; padding: 16px 24px; border-top: 1px solid var(--gray-150); }
.preview-composer__actions { display: flex; justify-content: flex-end; margin-top: 10px; }
@media (max-width: 520px) { .preview-history { padding: 16px; } .preview-composer { padding: 12px 16px; } }
</style>
