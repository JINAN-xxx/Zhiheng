<template>
  <a-drawer :open="modelValue" width="min(760px, 100vw)" :closable="!creating" :mask-closable="!creating" :keyboard="!creating" @close="close">
    <template #title><span class="agent-drawer-title">{{ agent?.name || '智能体' }} · API</span></template>
    <WorkspaceState v-if="!access" kind="empty" title="暂无已发布配置" />
    <template v-else>
      <div class="api-published"><a-tag color="green">已发布 v{{ agent.publishedVersion }}</a-tag><span>应用 {{ access.applicationId }}</span></div>
      <section class="api-section">
        <div class="api-section__heading"><h3>调用端点</h3><a-tooltip title="复制端点"><a-button type="text" aria-label="复制端点" @click="copy(endpoint)"><template #icon><CopyOutlined /></template></a-button></a-tooltip></div>
        <div class="api-endpoint"><a-tag color="blue">POST</a-tag><code>{{ endpoint }}</code></div>
        <div class="api-section__heading"><h3>请求示例</h3><a-tooltip title="复制请求示例"><a-button type="text" aria-label="复制请求示例" @click="copy(requestExample)"><template #icon><CopyOutlined /></template></a-button></a-tooltip></div>
        <pre class="api-code">{{ requestExample }}</pre>
        <p class="api-contract">使用此应用的 Bearer 密钥，需授权模型 {{ access.modelId }}。多轮对话通过 messages 传入，stream 为 true 时返回 SSE。</p>
      </section>
      <section class="api-section">
        <div class="api-section__heading"><h3>应用访问密钥</h3><a-tooltip v-if="can('ai:application:key:list')" title="刷新密钥"><a-button type="text" :loading="loading" aria-label="刷新密钥" @click="loadKeys"><template #icon><ReloadOutlined /></template></a-button></a-tooltip></div>
        <a-alert v-if="error" class="api-alert" type="error" :message="error" show-icon />
        <a-form v-if="can('ai:application:key:add')" class="api-key-form" :model="keyForm" layout="vertical" @finish="createKey">
          <a-form-item label="密钥名称" name="keyName" :rules="[{ required: true, whitespace: true, message: '请输入密钥名称' }]"><a-input v-model:value="keyForm.keyName" :maxlength="100" :disabled="creating" /></a-form-item>
          <a-button type="primary" html-type="submit" :loading="creating" :disabled="!!issuedKey"><template #icon><KeyOutlined /></template>创建密钥</a-button>
        </a-form>
        <div v-if="issuedKey" class="issued-key">
          <div class="api-section__heading"><strong>密钥仅显示一次</strong><a-tooltip title="隐藏明文密钥"><a-button type="text" aria-label="隐藏明文密钥" @click="clearIssuedKey"><template #icon><CloseOutlined /></template></a-button></a-tooltip></div>
          <code>{{ issuedKey.apiKey }}</code>
          <a-button @click="copy(issuedKey.apiKey)"><template #icon><CopyOutlined /></template>复制密钥</a-button>
        </div>
        <WorkspaceState v-if="!can('ai:application:key:list')" kind="forbidden" title="无权查看访问密钥" />
        <WorkspaceState v-else-if="loading && !keys.length" kind="loading" />
        <WorkspaceState v-else-if="!keys.length && !error" kind="empty" title="暂无访问密钥" />
        <div v-else class="api-keys">
          <article v-for="item in keys" :key="item.keyId" class="api-key-row">
            <div class="api-key-row__body"><strong>{{ item.keyName }}</strong><code>{{ item.maskedKey || item.fingerprint }}</code><span>{{ developerKeyStatusLabel(item.status) }}<template v-if="hasModelGrant(item)"> · 已授权此模型</template></span></div>
            <a-popconfirm v-if="can('ai:application:key:revoke') && ['active', 'retiring'].includes(item.status)" title="撤销后，该密钥在整个应用中立即失效。继续？" ok-text="撤销" cancel-text="取消" @confirm="revoke(item)"><a-tooltip title="撤销密钥"><a-button type="text" danger :loading="revokingId === item.keyId" aria-label="撤销密钥"><template #icon><DeleteOutlined /></template></a-button></a-tooltip></a-popconfirm>
          </article>
        </div>
      </section>
    </template>
  </a-drawer>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { CloseOutlined, CopyOutlined, DeleteOutlined, KeyOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import { buildAgentPublicPath, getPublishedAgentAccess } from '../../../utils/agent.js'
import { hasSkillPermission } from '../../../utils/skill.js'
import {
  createDeveloperKey, listDeveloperKeys, revokeDeveloperKey,
  developerKeyStatusLabel, extractIssuedKey, normalizeDeveloperKeys, buildPublicApiBase, copyText,
} from '../../../../ai/public/developerKeys.js'

const props = defineProps({ modelValue: Boolean, agent: { type: Object, default: null }, permissions: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const can = (permission) => hasSkillPermission(props.permissions, permission)
const access = computed(() => getPublishedAgentAccess(props.agent || {}))
const endpoint = computed(() => props.agent?.agentId ? `${buildPublicApiBase(window.location.origin, import.meta.env.VITE_APP_BASE_API)}${buildAgentPublicPath(props.agent.agentId).slice(3)}` : '')
const requestExample = computed(() => `curl '${endpoint.value}' \\\n  -H 'Authorization: Bearer <DEVELOPER_API_KEY>' \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify({ messages: [{ role: 'user', content: '你好' }], stream: false }, null, 2)}'`)
const keyForm = reactive({ keyName: '' })
const keys = ref([])
const loading = ref(false)
const creating = ref(false)
const revokingId = ref('')
const issuedKey = ref(null)
const error = ref('')
let generation = 0
let keyGeneration = 0

function hasModelGrant(key) { return key.grants.some((grant) => grant.status === 'active' && grant.modelId === access.value?.modelId) }

async function loadKeys() {
  if (!props.modelValue || !access.value || !can('ai:application:key:list')) return
  const requestId = ++keyGeneration
  loading.value = true
  error.value = ''
  try {
    const response = await listDeveloperKeys(access.value.applicationId)
    if (requestId === keyGeneration) keys.value = normalizeDeveloperKeys(response)
  } catch (reason) { if (requestId === keyGeneration) error.value = normalizeErrorMessage(reason, '密钥加载失败') }
  finally { if (requestId === keyGeneration) loading.value = false }
}

async function createKey() {
  if (!access.value || creating.value || issuedKey.value || !keyForm.keyName.trim() || !can('ai:application:key:add')) return
  const requestId = generation
  const { modelId, applicationId } = access.value
  creating.value = true; error.value = ''
  try {
    const response = await createDeveloperKey(applicationId, { keyName: keyForm.keyName.trim(), modelIds: [modelId], quota: { rpmLimit: 60, concurrencyLimit: 2 } })
    if (requestId !== generation || !props.modelValue) return
    issuedKey.value = extractIssuedKey(response)
    keyForm.keyName = ''
    await loadKeys()
  } catch (reason) { if (requestId === generation) error.value = normalizeErrorMessage(reason, '密钥创建失败') }
  finally { if (requestId === generation) creating.value = false }
}

async function revoke(item) {
  if (!access.value || !can('ai:application:key:revoke')) return
  const requestId = generation
  const applicationId = access.value.applicationId
  revokingId.value = item.keyId
  error.value = ''
  try {
    await revokeDeveloperKey(applicationId, item.keyId)
    if (requestId !== generation) return
    if (issuedKey.value?.keyId === item.keyId) clearIssuedKey()
    await loadKeys()
  } catch (reason) { if (requestId === generation) error.value = normalizeErrorMessage(reason, '密钥撤销失败') }
  finally { if (requestId === generation) revokingId.value = '' }
}

async function copy(value) {
  if (await copyText(value)) feedback.msgSuccess('已复制')
  else feedback.msgError('复制失败')
}

function clearIssuedKey() { issuedKey.value = null }
function reset() {
  generation += 1; keyGeneration += 1
  clearIssuedKey()
  keys.value = []; keyForm.keyName = ''; error.value = ''; loading.value = false; creating.value = false; revokingId.value = ''
}
function close() { if (creating.value) return; reset(); emit('update:modelValue', false) }
watch(() => [props.modelValue, props.agent?.agentId, props.agent?.publishedVersion], () => { reset(); if (props.modelValue) { keyForm.keyName = `${props.agent?.name || '智能体'} API`.slice(0, 100); loadKeys() } })
onBeforeUnmount(reset)
</script>

<style scoped>
.agent-drawer-title { display: block; overflow-wrap: anywhere; }
.api-published { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 16px; color: var(--color-text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.api-section + .api-section { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--gray-150); }
.api-section__heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 38px; }
.api-section__heading h3 { margin: 0; color: var(--color-text); font-size: 14px; font-weight: 600; }
.api-endpoint { display: flex; align-items: flex-start; gap: 8px; padding: 12px 0; }
.api-endpoint code { min-width: 0; overflow-wrap: anywhere; font-size: 12px; line-height: 22px; }
.api-code { max-height: 320px; overflow: auto; padding: 14px; border: 1px solid var(--gray-150); border-radius: 6px; color: var(--color-text); background: var(--gray-50); font-size: 12px; line-height: 1.7; }
.api-contract { overflow-wrap: anywhere; color: var(--color-text-secondary); font-size: 12px; line-height: 1.7; }
.api-key-form { display: flex; align-items: flex-start; gap: 10px; padding: 12px 0; }
.api-key-form :deep(.ant-form-item) { min-width: 0; flex: 1; margin: 0; }
.api-key-form > .ant-btn { margin-top: 30px; }
.api-alert { margin-bottom: 12px; }
.issued-key { display: grid; gap: 10px; margin: 12px 0; padding: 14px; border: 1px solid var(--color-warning-500); border-radius: 6px; background: var(--color-warning-50); }
.issued-key code { overflow-wrap: anywhere; user-select: all; }
.issued-key > .ant-btn { justify-self: start; }
.api-key-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--gray-150); }
.api-key-row__body { display: grid; min-width: 0; gap: 5px; }
.api-key-row__body strong, .api-key-row__body code { overflow-wrap: anywhere; }
.api-key-row__body code, .api-key-row__body span { color: var(--color-text-secondary); font-size: 12px; }
</style>
