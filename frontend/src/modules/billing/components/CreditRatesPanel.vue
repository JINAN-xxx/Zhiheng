<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Tag as ATag
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createCreditRate, getCreditRates } from '../api/index.ts'
import type { CreditRate, CreditRateCreate, CreditRateInput } from '../api/index.ts'
import {
  creditTime,
  formatPoints,
  normalizeCreditPrices,
  rateError,
  sameCreditPrices
} from '../presentation.ts'
const formName = `billing-point-rate-${useId()}`
const props = defineProps<{ modelId: string; searchUnit: boolean; canWrite: boolean }>()
const cards = ref<CreditRate[]>([]),
  loading = ref(false),
  saving = ref(false),
  error = ref(''),
  ready = ref(false),
  saved = ref(false)
const scope = new RequestScope(),
  mutation = new RequestScope()
let retryBody: CreditRateCreate | null = null
let retrySignature = ''
const form = reactive<CreditRateInput & { reason: string }>({
  reason: '',
  billing_mode: 'token',
  is_free: false,
  input_points: '0',
  output_points: '0',
  cache_points: '0',
  search_points: '0'
})
const currentRate = computed(() => cards.value[0] ?? null)
const changed = computed(() => !currentRate.value || !sameCreditPrices(form, currentRate.value))
const disabled = computed(() => !props.canWrite || loading.value || saving.value || !ready.value)
function fillCurrent(): void {
  if (currentRate.value)
    Object.assign(form, normalizeCreditPrices(currentRate.value), { reason: '' })
}
watch(
  () => JSON.stringify(form),
  () => {
    if (!saving.value) saved.value = false
  }
)
const columns = [
  { title: '版本', dataIndex: 'version', key: 'version', width: 70 },
  { title: '积分价格', key: 'price' },
  { title: '创建时间', key: 'created', width: 170 }
]
watch(
  () => [props.modelId, props.searchUnit] as const,
  async () => {
    const context = scope.replace()
    mutation.replace()
    saving.value = false
    ready.value = false
    saved.value = false
    error.value = ''
    cards.value = []
    Object.assign(form, {
      reason: '',
      billing_mode: props.searchUnit ? 'search_unit' : 'token',
      is_free: false,
      input_points: '0',
      output_points: '0',
      cache_points: '0',
      search_points: '0'
    })
    retryBody = null
    retrySignature = ''
    loading.value = true
    try {
      const result = await getCreditRates(props.modelId, context.signal)
      if (context.isCurrent()) {
        cards.value = [...result].sort((left, right) => right.version - left.version)
        fillCurrent()
        ready.value = true
      }
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  scope.dispose()
  mutation.dispose()
})
function setFree(checked: boolean): void {
  if (disabled.value) return
  form.is_free = checked
  if (checked)
    Object.assign(form, {
      input_points: '0',
      output_points: '0',
      cache_points: '0',
      search_points: '0'
    })
}
async function save(): Promise<void> {
  if (disabled.value || !changed.value) return
  const body = {
    ...normalizeCreditPrices(form),
    reason: form.reason.trim()
  }
  error.value = rateError(body)
  if (error.value) return
  const signature = JSON.stringify(body)
  if (!retryBody || signature !== retrySignature) {
    retrySignature = signature
    retryBody = { ...body, client_request_id: createRequestId() }
  }
  const id = props.modelId,
    context = mutation.replace()
  saving.value = true
  try {
    const result = await createCreditRate(id, retryBody, context.signal)
    if (!context.isCurrent() || props.modelId !== id) return
    const refresh = scope.replace()
    cards.value = [result, ...cards.value.filter((card) => card.id !== result.id)].sort(
      (left, right) => right.version - left.version
    )
    fillCurrent()
    retryBody = null
    retrySignature = ''
    saved.value = true
    loading.value = true
    try {
      const history = await getCreditRates(id, refresh.signal)
      if (refresh.isCurrent()) {
        const versions = new Map([...history, ...cards.value].map((card) => [card.id, card]))
        cards.value = [...versions.values()].sort((left, right) => right.version - left.version)
        fillCurrent()
      }
    } catch (reason) {
      if (refresh.isCurrent() && !isAborted(reason))
        error.value = `价格已保存，调整记录刷新失败：${errorMessage(reason)}`
    } finally {
      if (refresh.isCurrent()) loading.value = false
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
</script>
<template>
  <section aria-label="模型积分价格">
    <AAlert v-if="error" :message="error" type="error" show-icon class="rate-notice" />
    <header class="rate-heading">
      <h3>当前积分价格</h3>
      <span>{{ searchUnit ? '积分 / 检索单元' : '积分 / 百万 Token' }}</span>
      <ATag v-if="ready && !currentRate" color="warning">未设置</ATag>
    </header>
    <p v-if="currentRate" class="rate-updated">最近更新 {{ creditTime(currentRate.created_at) }}</p>
    <p v-else-if="ready" class="rate-hint">启用积分计费前，请设置价格或设为免费。</p>
    <AForm :name="formName" :model="form" layout="vertical" @submit.prevent="save">
      <AFormItem
        ><ACheckbox :checked="form.is_free" :disabled="disabled" @update:checked="setFree"
          >设为免费</ACheckbox
        ></AFormItem
      >
      <div v-if="!searchUnit" class="rate-grid">
        <AFormItem label="输入" name="input_points"
          ><AInput
            v-model:value="form.input_points"
            aria-label="输入积分 / 百万 Token"
            :disabled="form.is_free || disabled"
        /></AFormItem>
        <AFormItem label="输出" name="output_points"
          ><AInput
            v-model:value="form.output_points"
            aria-label="输出积分 / 百万 Token"
            :disabled="form.is_free || disabled"
        /></AFormItem>
        <AFormItem label="缓存" name="cache_points"
          ><AInput
            v-model:value="form.cache_points"
            aria-label="缓存积分 / 百万 Token"
            :disabled="form.is_free || disabled"
        /></AFormItem>
      </div>
      <AFormItem v-else label="检索" name="search_points"
        ><AInput
          v-model:value="form.search_points"
          aria-label="积分 / 检索单元"
          :disabled="form.is_free || disabled"
      /></AFormItem>
      <AFormItem v-if="canWrite" label="备注" name="reason"
        ><AInput.TextArea
          v-model:value="form.reason"
          :disabled="disabled"
          :auto-size="{ minRows: 1, maxRows: 3 }"
          :maxlength="1000"
          placeholder="选填，可说明本次价格调整"
      /></AFormItem>
      <p class="rate-hint">
        <template v-if="!searchUnit">缓存命中部分按缓存单价计费。</template>保存后仅影响新的调用。
      </p>
      <div v-if="canWrite" class="rate-submit">
        <span v-if="saved" class="rate-saved" role="status">价格已保存</span>
        <AButton type="primary" :loading="saving" :disabled="disabled || !changed" @click="save"
          >保存价格</AButton
        >
      </div>
    </AForm>
    <details v-if="cards.length" class="rate-history">
      <summary>
        价格调整记录<span>{{ cards.length }} 条</span>
      </summary>
      <ResourceTable :columns="columns" :rows="cards" :loading="loading" :scroll-x="'100%'">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'price'">
            <ATag v-if="record.is_free" color="success">免费</ATag>
            <span v-else-if="record.billing_mode === 'search_unit'"
              >{{ formatPoints(record.search_points) }} 积分 / 检索单元</span
            >
            <div v-else class="rate-lines">
              <span
                >输入 {{ formatPoints(record.input_points) }} / 输出
                {{ formatPoints(record.output_points) }}</span
              >
              <span>缓存 {{ formatPoints(record.cache_points) }} 积分 / 百万 Token</span>
            </div>
          </template>
          <span v-else-if="column.key === 'created'">{{ creditTime(record.created_at) }}</span>
        </template>
      </ResourceTable>
    </details>
  </section>
</template>
<style scoped>
.rate-notice {
  margin-bottom: 16px;
}
.rate-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  margin: 4px 0 10px;
}
.rate-heading h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
}
.rate-heading > span,
.rate-updated {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.rate-updated {
  margin: 0 0 18px;
}
.rate-history {
  margin-top: 20px;
  border-top: 1px solid var(--gray-150);
}
.rate-history summary {
  padding: 14px 0;
  cursor: pointer;
  font-size: 13px;
  color: var(--color-text-secondary);
}
.rate-history summary span {
  margin-left: 8px;
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.rate-history summary:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.rate-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.rate-lines {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.rate-hint {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin: 0 0 16px;
}
.rate-submit {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
}
.rate-saved {
  color: var(--color-text-secondary);
  font-size: 12px;
}
@media (max-width: 640px) {
  .rate-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
