<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <a-drawer
    :open="modelValue"
    width="min(760px, 100vw)"
    :closable="!creating"
    :mask-closable="!creating"
    :keyboard="!creating"
    @close="close"
  >
    <template #title
      ><span class="agent-drawer-title">{{ agent?.name || '智能体' }} · API</span></template
    >
    <WorkspaceState v-if="!access" kind="empty" title="暂无已发布配置" />
    <template v-else>
      <div class="api-published">
        <a-tag color="green">已发布 v{{ agent?.publishedVersion }}</a-tag
        ><span>应用 {{ access.applicationId }}</span>
      </div>
      <section class="api-section">
        <div class="api-section__heading">
          <h3>调用端点</h3>
          <a-tooltip title="复制端点"
            ><a-button type="text" aria-label="复制端点" @click="copy(endpoint)"
              ><template #icon><CopyOutlined /></template></a-button
          ></a-tooltip>
        </div>
        <div class="api-endpoint">
          <a-tag color="blue">POST</a-tag><code>{{ endpoint }}</code>
        </div>
        <div class="api-section__heading">
          <h3>请求示例</h3>
          <a-tooltip title="复制请求示例"
            ><a-button type="text" aria-label="复制请求示例" @click="copy(requestExample)"
              ><template #icon><CopyOutlined /></template></a-button
          ></a-tooltip>
        </div>
        <pre class="api-code">{{ requestExample }}</pre>
        <p class="api-contract">
          使用此应用的 Bearer 密钥，需授权模型 {{ access.modelId }}。多轮对话通过 messages
          传入，stream 为 true 时返回 SSE。
        </p>
      </section>
      <section class="api-section">
        <div class="api-section__heading">
          <h3>应用访问密钥</h3>
          <a-tooltip title="刷新密钥"
            ><a-button type="text" :loading="loading" aria-label="刷新密钥" @click="loadKeys"
              ><template #icon><ReloadOutlined /></template></a-button
          ></a-tooltip>
        </div>
        <a-alert v-if="error" class="api-alert" type="error" :message="error" show-icon />
        <a-form class="api-key-form" :model="keyForm" layout="vertical" @finish="createKey">
          <a-form-item
            label="密钥名称"
            name="keyName"
            :rules="[{ required: true, whitespace: true, message: '请输入密钥名称' }]"
            ><a-input v-model:value="keyForm.keyName" :maxlength="80" :disabled="creating"
          /></a-form-item>
          <a-button
            type="primary"
            html-type="submit"
            aria-label="创建密钥"
            :loading="creating"
            :disabled="!!issuedKey || !!revokingId"
            ><template #icon><KeyOutlined /></template>创建密钥</a-button
          >
        </a-form>
        <div v-if="issuedKey" class="issued-key">
          <div class="api-section__heading">
            <strong>密钥仅显示一次</strong
            ><a-tooltip title="隐藏明文密钥"
              ><a-button type="text" aria-label="隐藏明文密钥" @click="clearIssuedKey"
                ><template #icon><CloseOutlined /></template></a-button
            ></a-tooltip>
          </div>
          <code>{{ issuedKey.apiKey }}</code>
          <a-button @click="copy(issuedKey.apiKey)"
            ><template #icon><CopyOutlined /></template>复制密钥</a-button
          >
        </div>
        <WorkspaceState v-if="loading && !keys.length" kind="loading" />
        <WorkspaceState v-else-if="!keys.length && !error" kind="empty" title="暂无访问密钥" />
        <div v-else class="api-keys">
          <article v-for="item in keys" :key="item.keyId" class="api-key-row">
            <div class="api-key-row__body">
              <strong>{{ item.keyName }}</strong
              ><code>{{ item.maskedKey }}</code
              ><span
                >{{ developerKeyStatusLabel(item.status)
                }}<template v-if="hasModelGrant(item)"> · 已授权此模型</template></span
              >
            </div>
            <a-popconfirm
              v-if="['active', 'retiring'].includes(item.status)"
              title="撤销后，该密钥在整个应用中立即失效。继续？"
              ok-text="撤销"
              cancel-text="取消"
              @confirm="revoke(item)"
              ><a-tooltip title="撤销密钥"
                ><a-button
                  type="text"
                  danger
                  :loading="revokingId === item.keyId"
                  aria-label="撤销密钥"
                  ><template #icon><DeleteOutlined /></template></a-button></a-tooltip
            ></a-popconfirm>
          </article>
        </div>
      </section>
    </template>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Drawer as ADrawer,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Popconfirm as APopconfirm,
  Tag as ATag,
  Tooltip as ATooltip,
  message
} from 'ant-design-vue'
import {
  CloseOutlined,
  CopyOutlined,
  DeleteOutlined,
  KeyOutlined,
  ReloadOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import {
  createDeveloperKey,
  listDeveloperKeys,
  revokeDeveloperKey,
  developerKeyStatusLabel
} from '../../developer/public.ts'
import type { DeveloperKey, IssuedKey } from '../../developer/public.ts'
import type { Agent } from '../api/index.ts'
import { getPublishedAgentAccess } from '../presentation.ts'
const props = defineProps<{ modelValue: boolean; agent: Agent | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const access = computed(() => getPublishedAgentAccess(props.agent))
const endpoint = computed(() =>
  props.agent
    ? `${window.location.origin}/v1/agents/${encodeURIComponent(props.agent.agentId)}/chat/completions`
    : ''
)
const requestExample = computed(
  () => `curl '${endpoint.value}' \
  -H 'Authorization: Bearer <DEVELOPER_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '${JSON.stringify({ messages: [{ role: 'user', content: '你好' }], stream: false }, null, 2)}'`
)
const keyForm = reactive({ keyName: '' }),
  keys = ref<DeveloperKey[]>([]),
  loading = ref(false),
  creating = ref(false),
  revokingId = ref(''),
  issuedKey = ref<IssuedKey | null>(null),
  error = ref('')
const readScope = new RequestScope(),
  mutationScope = new RequestScope()
const hasModelGrant = (key: DeveloperKey): boolean =>
  key.grants.some((grant) => grant.modelId === access.value?.modelId)
async function loadKeys(): Promise<void> {
  const binding = access.value
  if (!props.modelValue || !binding) return
  const context = readScope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await listDeveloperKeys(binding.applicationId, context.signal)
    if (context.isCurrent()) keys.value = result
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
async function createKey(): Promise<void> {
  const binding = access.value
  if (!binding || creating.value || revokingId.value || issuedKey.value || !keyForm.keyName.trim())
    return
  const context = mutationScope.replace()
  creating.value = true
  error.value = ''
  try {
    const result = await createDeveloperKey(
      binding.applicationId,
      {
        name: keyForm.keyName.trim(),
        model_ids: [binding.modelId],
        rpm_limit: 60,
        tpm_limit: null,
        concurrency_limit: 2,
        expires_at: null
      },
      context.signal
    )
    if (context.isCurrent() && props.modelValue) {
      issuedKey.value = result
      keyForm.keyName = ''
      await loadKeys()
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) creating.value = false
  }
}
async function revoke(item: DeveloperKey): Promise<void> {
  const binding = access.value
  if (!binding || revokingId.value || creating.value) return
  const context = mutationScope.replace()
  revokingId.value = item.keyId
  error.value = ''
  try {
    await revokeDeveloperKey(binding.applicationId, item.keyId, context.signal)
    if (context.isCurrent()) {
      if (issuedKey.value?.key.keyId === item.keyId) clearIssuedKey()
      await loadKeys()
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) revokingId.value = ''
  }
}
async function copy(value: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(value)
    void message.success('已复制')
  } catch {
    void message.error('复制失败')
  }
}
function clearIssuedKey(): void {
  issuedKey.value = null
}
function reset(): void {
  readScope.replace()
  mutationScope.replace()
  issuedKey.value = null
  keys.value = []
  keyForm.keyName = ''
  error.value = ''
  loading.value = false
  creating.value = false
  revokingId.value = ''
}
function close(): void {
  if (creating.value) return
  reset()
  emit('update:modelValue', false)
}
watch(
  () => [props.modelValue, props.agent?.agentId, props.agent?.publishedVersion],
  () => {
    reset()
    if (props.modelValue) {
      keyForm.keyName = `${props.agent?.name ?? '智能体'} API`.slice(0, 80)
      void loadKeys()
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  readScope.dispose()
  mutationScope.dispose()
  issuedKey.value = null
})
</script>

<style scoped>
.agent-drawer-title {
  display: block;
  overflow-wrap: anywhere;
}
.api-published {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 16px;
  color: var(--color-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.api-section + .api-section {
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--gray-150);
}
.api-section__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
}
.api-section__heading h3 {
  margin: 0;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 600;
}
.api-endpoint {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 0;
}
.api-endpoint code {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 22px;
}
.api-code {
  max-height: 320px;
  overflow: auto;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  color: var(--color-text);
  background: var(--gray-50);
  font-size: 12px;
  line-height: 1.7;
}
.api-contract {
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.7;
}
.api-key-form {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 0;
}
.api-key-form :deep(.ant-form-item) {
  min-width: 0;
  flex: 1;
  margin: 0;
}
.api-key-form > .ant-btn {
  margin-top: 30px;
}
.api-alert {
  margin-bottom: 12px;
}
.issued-key {
  display: grid;
  gap: 10px;
  margin: 12px 0;
  padding: 14px;
  border: 1px solid var(--color-warning-500);
  border-radius: 6px;
  background: var(--color-warning-50);
}
.issued-key code {
  overflow-wrap: anywhere;
  user-select: all;
}
.issued-key > .ant-btn {
  justify-self: start;
}
.api-key-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 0;
  border-bottom: 1px solid var(--gray-150);
}
.api-key-row__body {
  display: grid;
  min-width: 0;
  gap: 5px;
}
.api-key-row__body strong,
.api-key-row__body code {
  overflow-wrap: anywhere;
}
.api-key-row__body code,
.api-key-row__body span {
  color: var(--color-text-secondary);
  font-size: 12px;
}
</style>
