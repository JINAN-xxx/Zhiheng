<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import {
  Alert as AAlert,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal as AModal
} from 'ant-design-vue'
import { createRequestId } from '../../../shared/requestId.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { reconcileCreditUsage } from '../api/index.ts'
import type { CreditReconciliation } from '../api/types.ts'
import { reconcileInvocationTokens } from '../api/trace.ts'
import { hasConfirmedUsage, reconciliationDraft, reconciliationPayload } from '../reconciliation.ts'
import type { ReconciliationDraft } from '../reconciliation.ts'
import type { ReconciliationTarget } from '../trace.ts'

const props = defineProps<{ target: ReconciliationTarget | null }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const selected = computed(() => props.target)
const canWrite = computed(() => hasAnyPermission(identity.value, 'ai:credits:reconcile'))
const formName = `billing-reconciliation-${useId()}`
const mutation = new RequestScope()
const saving = ref(false),
  formError = ref('')
const form = reactive<ReconciliationDraft>(
  reconciliationDraft({
    input_tokens: null,
    output_tokens: null,
    cache_tokens: null,
    search_units: null
  })
)
let retryBody: CreditReconciliation | null = null,
  retrySignature = ''
watch(
  () => props.target,
  (value) => {
    mutation.replace()
    saving.value = false
    formError.value = ''
    retryBody = null
    retrySignature = ''
    if (value) Object.assign(form, reconciliationDraft(value))
  },
  { immediate: true }
)
watch(
  () => identity.value,
  () => {
    mutation.replace()
    emit('close')
  }
)
onBeforeUnmount(() => mutation.dispose())
function close() {
  if (!saving.value) emit('close')
}
function setCount(
  field: 'input_tokens' | 'output_tokens' | 'cache_tokens' | 'search_units',
  value: unknown
) {
  form[field] =
    value === null || value === undefined || value === ''
      ? null
      : typeof value === 'number'
        ? value
        : Number.NaN
}
async function reconcile() {
  const row = selected.value
  if (!row || !canWrite.value || saving.value) return
  formError.value = ''
  let payload: Omit<CreditReconciliation, 'client_request_id'>
  try {
    payload = reconciliationPayload(row, form)
    if (
      !payload.rejected_before_execution &&
      row.requires_search_units &&
      payload.search_units === null
    )
      throw new Error('请确认实际检索单元；未知值不能按零提交')
  } catch (reason) {
    formError.value = errorMessage(reason)
    return
  }
  const signature = JSON.stringify([row.kind, row.run_id, payload])
  if (!retryBody || retrySignature !== signature) {
    retrySignature = signature
    retryBody = { ...payload, client_request_id: createRequestId() }
  }
  const context = mutation.replace()
  saving.value = true
  try {
    await (row.kind === 'credits' ? reconcileCreditUsage : reconcileInvocationTokens)(
      row.run_id,
      retryBody,
      context.signal
    )
    if (context.isCurrent()) emit('changed')
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason))
      formError.value =
        reason instanceof ApiError &&
        ['version_conflict', 'usage_version_conflict'].includes(reason.code)
          ? '记录版本已变化，请关闭弹窗并刷新后重新核对'
          : errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
</script>
<template>
  <AModal
    :open="selected !== null"
    :title="selected?.kind === 'tokens' ? '核对未知 Token 用量' : '核对积分消费'"
    :confirm-loading="saving"
    :mask-closable="!saving"
    :closable="!saving"
    :cancel-button-props="{ disabled: saving }"
    ok-text="确认核对"
    cancel-text="取消"
    @ok="reconcile"
    @cancel="close"
  >
    <AAlert v-if="formError" :message="formError" type="error" show-icon class="activity-alert" />
    <p>
      运行 {{ selected?.run_id }} · 版本 {{ selected?.version }} ·
      {{ selected?.billing_mode === 'search_unit' ? '按检索单元计费' : '按 Token 计费' }}
    </p>
    <AAlert
      message="仅填写已核实的实际用量。无法确认时请保留待核对，预占不会自动释放。"
      type="info"
      show-icon
      class="activity-alert"
    />
    <AAlert
      :message="
        selected?.billing_mode === 'search_unit'
          ? '请确认实际检索单元。供应商未提供的 Token 保留为未知，无须填写。'
          : '请补齐实际输入与输出 Token。已有确认值会保留；缓存 Token 未提供时可留空。'
      "
      type="info"
      show-icon
      class="activity-alert"
    />
    <AForm :name="formName" layout="vertical" :model="form">
      <AFormItem
        ><ACheckbox
          v-model:checked="form.rejected_before_execution"
          :disabled="saving || (selected !== null && hasConfirmedUsage(selected))"
          >已确认在执行前被拒绝，释放全部预占</ACheckbox
        ></AFormItem
      >
      <div v-if="!form.rejected_before_execution" class="reconcile-grid">
        <template v-if="selected?.billing_mode === 'token'">
          <AFormItem label="实际输入 Token" name="input_tokens" required
            ><AInputNumber
              :value="form.input_tokens ?? ''"
              :min="0"
              :max="2147483647"
              :precision="0"
              :disabled="saving || selected?.input_tokens != null"
              placeholder="未知，请核实后填写"
              @update:value="(value) => setCount('input_tokens', value)"
          /></AFormItem>
          <AFormItem label="实际输出 Token" name="output_tokens" required
            ><AInputNumber
              :value="form.output_tokens ?? ''"
              :min="0"
              :max="2147483647"
              :precision="0"
              :disabled="saving || selected?.output_tokens != null"
              placeholder="未知，请核实后填写"
              @update:value="(value) => setCount('output_tokens', value)"
          /></AFormItem>
          <AFormItem label="其中缓存 Token" name="cache_tokens"
            ><AInputNumber
              :value="form.cache_tokens ?? ''"
              :min="0"
              :max="2147483647"
              :precision="0"
              :disabled="saving || selected?.cache_tokens != null"
              placeholder="未提供，可留空"
              @update:value="(value) => setCount('cache_tokens', value)"
          /></AFormItem>
        </template>
        <AFormItem
          v-if="selected?.billing_mode === 'search_unit' || selected?.requires_search_units"
          label="实际检索单元"
          name="search_units"
          required
          ><AInputNumber
            :value="form.search_units ?? ''"
            :min="0"
            :max="2147483647"
            :precision="0"
            :disabled="saving || selected?.search_units != null"
            placeholder="未知，请核实后填写"
            @update:value="(value) => setCount('search_units', value)"
        /></AFormItem>
      </div>
      <AFormItem label="核对依据" name="reason" required
        ><AInput.TextArea
          v-model:value="form.reason"
          :maxlength="1000"
          :rows="3"
          :disabled="saving"
          placeholder="填写供应商记录或执行前拒绝的核实依据"
      /></AFormItem>
    </AForm>
  </AModal>
</template>
<style scoped>
.activity-alert {
  margin-bottom: 14px;
}
.reconcile-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 12px;
}
.reconcile-grid :deep(.ant-input-number) {
  width: 100%;
}
</style>
