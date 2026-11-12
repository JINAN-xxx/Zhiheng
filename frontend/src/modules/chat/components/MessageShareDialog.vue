<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Modal as AModal,
  Select as ASelect,
  Spin as ASpin,
  message as notice
} from 'ant-design-vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { copyText } from '../../../shared/clipboard.ts'
import {
  createMessageShare,
  getMessageShares,
  getSharePreview,
  revokeMessageShare
} from '../api/messageActions.ts'
import type { CreatedMessageShare, MessageShare, SharePreview } from '../api/messageActions.ts'
import AiMessage from './AiMessage.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
const props = defineProps<{ messageId: string }>()
const emit = defineEmits<{ close: [] }>()
const includeQuestion = ref(true)
const expiresInDays = ref<1 | 7 | 30>(7)
const preview = ref<SharePreview | null>(null)
const shares = ref<MessageShare[]>([])
const created = ref<CreatedMessageShare | null>(null)
const loading = ref(false)
const sharesLoading = ref(false)
const busy = ref(false)
const error = ref('')
const scope = new RequestScope()
const previewScope = new RequestScope()
const sharesScope = new RequestScope()
const shareLink = computed(() =>
  created.value ? `${window.location.origin}/share/${encodeURIComponent(created.value.token)}` : ''
)
const expiryOptions = [
  { value: 1, label: '1天' },
  { value: 7, label: '7天' },
  { value: 30, label: '30天' }
]
function setExpiry(value: unknown): void {
  if (value === 1 || value === 7 || value === 30) expiresInDays.value = value
}
async function loadPreview(): Promise<void> {
  const context = previewScope.replace()
  const include = includeQuestion.value
  preview.value = null
  loading.value = true
  error.value = ''
  try {
    const value = await getSharePreview(props.messageId, include, context.signal)
    if (!context.isCurrent()) return
    if (value.message_id !== props.messageId) throw new Error('分享预览与当前回复不匹配')
    preview.value = value
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
async function loadShares(): Promise<void> {
  const context = sharesScope.replace()
  sharesLoading.value = true
  try {
    const values = await getMessageShares(props.messageId, context.signal)
    if (context.isCurrent()) shares.value = values
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) sharesLoading.value = false
  }
}
watch(
  () => [props.messageId, includeQuestion.value],
  () => {
    void loadPreview()
  },
  { immediate: true }
)
void loadShares()
async function create(): Promise<void> {
  if (!preview.value || loading.value || busy.value || created.value) return
  const context = scope.current()
  sharesScope.replace()
  sharesLoading.value = false
  busy.value = true
  error.value = ''
  try {
    const value = await createMessageShare(
      props.messageId,
      {
        include_question: includeQuestion.value,
        expires_in_days: expiresInDays.value,
        expected_content_hash: preview.value.content_hash
      },
      context.signal
    )
    if (!context.isCurrent()) return
    if (value.message_id !== props.messageId) throw new Error('分享链接与当前回复不匹配')
    created.value = value
    shares.value = [value, ...shares.value.filter((item) => item.id !== value.id)]
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      if (reason instanceof ApiError && reason.status === 409) {
        await loadPreview()
        if (context.isCurrent()) error.value = '回复内容已变化，请核对最新预览后重新创建分享'
      } else
        error.value = `${errorMessage(reason)}${!(reason instanceof ApiError) || reason.status >= 500 ? '；创建结果待确认，请刷新分享列表核对。' : ''}`
    }
  } finally {
    if (context.isCurrent()) busy.value = false
  }
}
async function copy(): Promise<void> {
  try {
    await copyText(shareLink.value)
    notice.success('分享链接已复制')
  } catch (reason) {
    notice.error(errorMessage(reason))
  }
}
async function revoke(share: MessageShare): Promise<void> {
  if (busy.value || share.revoked_at) return
  const context = scope.current()
  busy.value = true
  error.value = ''
  try {
    await revokeMessageShare(share.id, context.signal)
    if (!context.isCurrent()) return
    if (created.value?.id === share.id) created.value = null
    await loadShares()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) busy.value = false
  }
}
function close(): void {
  scope.dispose()
  previewScope.dispose()
  sharesScope.dispose()
  created.value = null
  emit('close')
}
onBeforeUnmount(() => {
  scope.dispose()
  previewScope.dispose()
  sharesScope.dispose()
  created.value = null
})
</script>
<template>
  <AModal :open="true" title="分享回复" :footer="null" :width="680" @cancel="close">
    <div class="message-share">
      <p class="muted">对方需能访问本平台地址。链接只展示下方提问与回复文本。</p>
      <div class="share-options">
        <ACheckbox v-model:checked="includeQuestion" :disabled="busy || Boolean(created)"
          >附带提问</ACheckbox
        >
        <label class="share-expiry"
          >有效期
          <ASelect
            :value="expiresInDays"
            aria-label="分享有效期"
            :options="expiryOptions"
            :disabled="busy || Boolean(created)"
            @update:value="setExpiry"
        /></label>
      </div>
      <AAlert v-if="error" type="error" :message="error" show-icon />
      <ASpin v-if="loading" />
      <section v-else-if="preview" class="share-preview" aria-label="分享预览">
        <template v-if="preview.question !== null"
          ><h3>提问</h3>
          <p class="share-question">{{ preview.question }}</p></template
        >
        <h3>回复</h3>
        <AiMessage :content="preview.answer" />
      </section>
      <AButton
        v-if="!created"
        type="primary"
        :disabled="loading || !preview"
        :loading="busy"
        @click="create"
        >创建分享链接</AButton
      >
      <div v-else class="share-created" role="status">
        <span class="share-link">{{ shareLink }}</span>
        <AButton @click="copy">复制分享链接</AButton>
      </div>
      <section class="share-history" aria-label="已创建的分享">
        <div class="share-history-title">
          <h3>已创建的分享</h3>
          <RefreshButton
            label="刷新分享列表"
            :loading="sharesLoading"
            :disabled="busy"
            @click="loadShares"
          />
        </div>
        <p v-if="!shares.length" class="muted">暂无分享链接</p>
        <div v-for="share in shares" :key="share.id" class="share-record">
          <div>
            <span>{{
              share.revoked_at
                ? '已撤销'
                : new Date(share.expires_at).getTime() <= Date.now()
                  ? '已过期'
                  : '有效'
            }}</span
            ><span class="muted"> · {{ new Date(share.expires_at).toLocaleString() }} 到期</span>
          </div>
          <AButton v-if="!share.revoked_at" size="small" :disabled="busy" @click="revoke(share)"
            >撤销分享</AButton
          >
        </div>
      </section>
    </div>
  </AModal>
</template>
<style scoped>
.message-share {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.message-share p {
  margin: 0;
}
.share-options,
.share-expiry,
.share-created,
.share-record,
.share-history-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.share-options,
.share-record,
.share-history-title {
  justify-content: space-between;
}
.share-expiry :deep(.ant-select) {
  width: 90px;
}
.share-preview {
  max-height: min(45vh, 460px);
  overflow-y: auto;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.message-share h3 {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 500;
}
.share-preview h3:not(:first-child) {
  margin-top: 20px;
}
.share-question {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.share-link {
  overflow-wrap: anywhere;
  user-select: all;
  flex: 1 1 230px;
}
.share-record {
  padding: 10px 0;
  gap: 8px;
}
.share-history-title h3 {
  margin: 0;
}
.share-history {
  border-top: 1px solid var(--gray-150);
  padding-top: 16px;
}
</style>
