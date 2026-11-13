<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, onMounted, reactive, ref, useId, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Switch as ASwitch,
  Tag as ATag
} from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getCreditState, setCreditState } from '../api/index.ts'
import type { CreditState, CreditStateMutation } from '../api/index.ts'
import { creditAccess } from '../presentation.ts'

const emit = defineEmits<{
  state: [value: CreditState | null]
  loading: [value: boolean]
  changed: []
}>()
const formName = `billing-mode-${useId()}`
const canSwitch = computed(() => creditAccess(identity.value).switchMode)
const state = ref<CreditState | null>(null),
  loading = ref(false),
  error = ref(''),
  open = ref(false),
  saving = ref(false),
  formError = ref('')
const form = reactive({ enabled: false, reason: '' })
const reads = new RequestScope(),
  writes = new RequestScope()
let version = 0,
  retryBody: CreditStateMutation | null = null,
  retrySignature = ''
watch(
  () => loading.value || saving.value,
  (value) => emit('loading', value),
  { immediate: true }
)

async function refresh(): Promise<void> {
  const ticket = reads.replace()
  loading.value = true
  error.value = ''
  try {
    const value = await getCreditState(ticket.signal)
    if (ticket.isCurrent()) {
      state.value = value
      emit('state', value)
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      state.value = null
      emit('state', null)
      error.value = errorMessage(reason)
    }
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
function edit(): void {
  if (!canSwitch.value || !state.value || loading.value || saving.value) return
  form.enabled = state.value.enabled
  form.reason = ''
  version = state.value.version
  formError.value = ''
  retryBody = null
  retrySignature = ''
  open.value = true
}
function close(): void {
  if (!saving.value) open.value = false
}
async function save(): Promise<void> {
  if (!canSwitch.value || !state.value || saving.value || form.enabled === state.value.enabled)
    return
  const payload = { enabled: form.enabled, expected_version: version, reason: form.reason.trim() }
  const signature = JSON.stringify(payload)
  if (!retryBody || signature !== retrySignature) {
    retrySignature = signature
    retryBody = { ...payload, client_request_id: createRequestId() }
  }
  reads.replace()
  loading.value = false
  const ticket = writes.replace()
  saving.value = true
  formError.value = ''
  try {
    const value = await setCreditState(retryBody, ticket.signal)
    if (!ticket.isCurrent()) return
    state.value = value
    emit('state', value)
    open.value = false
    emit('changed')
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason))
      formError.value =
        reason instanceof ApiError && reason.status === 409 && reason.code === 'version_conflict'
          ? '计费状态已变化，请关闭设置并刷新后重试。'
          : errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
onMounted(() => void refresh())
onBeforeUnmount(() => {
  reads.dispose()
  writes.dispose()
  emit('loading', false)
})
defineExpose({ refresh })
</script>
<template>
  <div class="credit-billing-control" aria-label="积分计费管理">
    <ATag v-if="state && !error" :color="state.enabled ? 'success' : 'default'">
      {{ state.enabled ? '积分计费已启用' : '积分计费已停用' }}
    </ATag>
    <AButton v-if="error" type="text" danger :title="error" :loading="loading" @click="refresh"
      >重试计费状态</AButton
    >
    <AButton v-if="canSwitch" :disabled="!state || loading || saving" @click="edit"
      >计费设置</AButton
    >
    <AModal
      :open="open"
      title="积分计费设置"
      ok-text="保存设置"
      cancel-text="取消"
      :confirm-loading="saving"
      :ok-button-props="{ disabled: !state || form.enabled === state.enabled }"
      :cancel-button-props="{ disabled: saving }"
      :mask-closable="!saving"
      :closable="!saving"
      @ok="save"
      @cancel="close"
    >
      <AAlert
        v-if="formError"
        :message="formError"
        type="error"
        show-icon
        class="billing-setting-notice"
      />
      <AForm :name="formName" :model="form" layout="vertical">
        <AFormItem label="启用积分计费" name="enabled">
          <ASwitch
            :checked="form.enabled"
            :disabled="saving"
            aria-label="启用积分计费"
            @change="(value) => (form.enabled = value === true)"
          />
        </AFormItem>
        <AAlert
          v-if="state && form.enabled !== state.enabled"
          :message="
            form.enabled
              ? '请先配置模型积分价格并准备用户余额。启用后，未配置价格或余额不足的调用将被拒绝。'
              : '停用后新调用仅统计 Token，已有预占继续按原价格结算。'
          "
          type="warning"
          show-icon
          class="billing-setting-notice"
        />
        <AFormItem label="备注" name="reason">
          <AInput.TextArea
            v-model:value="form.reason"
            :disabled="saving"
            :maxlength="1000"
            :rows="3"
            placeholder="选填"
          />
        </AFormItem>
      </AForm>
    </AModal>
  </div>
</template>
<style scoped>
.credit-billing-control {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.credit-billing-control > .ant-tag {
  margin: 0;
}
.billing-setting-notice {
  margin-bottom: 16px;
}
</style>
