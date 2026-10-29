<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Divider as ADivider,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Tag as ATag
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createCreditRate, getCreditRates } from '../api/index.ts'
import type { CreditRate, CreditRateCreate, CreditRateInput } from '../api/index.ts'
import { creditTime, formatPoints, rateError } from '../presentation.ts'
const formName = `billing-point-rate-${useId()}`
const props = defineProps<{ modelId: string; searchUnit: boolean; canWrite: boolean }>()
const cards = ref<CreditRate[]>([]),
  loading = ref(false),
  saving = ref(false),
  error = ref('')
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
      if (context.isCurrent()) cards.value = result
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
  if (!props.canWrite || saving.value) return
  const body = {
    ...form,
    input_points: form.input_points.trim(),
    output_points: form.output_points.trim(),
    cache_points: form.cache_points.trim(),
    search_points: form.search_points.trim()
  }
  error.value = rateError(body)
  if (error.value) return
  body.reason = form.reason.trim()
  const signature = JSON.stringify(body)
  if (!retryBody || signature !== retrySignature) {
    retrySignature = signature
    retryBody = { ...body, client_request_id: crypto.randomUUID() }
  }
  const id = props.modelId,
    context = mutation.replace()
  saving.value = true
  try {
    const result = await createCreditRate(id, retryBody, context.signal)
    if (!context.isCurrent() || props.modelId !== id) return
    const refresh = scope.replace()
    cards.value = [result, ...cards.value.filter((card) => card.id !== result.id)]
    loading.value = true
    try {
      const history = await getCreditRates(id, refresh.signal)
      if (refresh.isCurrent()) {
        const versions = new Map([...history, ...cards.value].map((card) => [card.id, card]))
        cards.value = [...versions.values()].sort((left, right) => right.version - left.version)
      }
    } catch (reason) {
      if (refresh.isCurrent() && !isAborted(reason))
        error.value = `积分价格已创建，历史刷新失败：${errorMessage(reason)}`
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
    <AAlert
      message="积分价格仅在启用积分计费后使用，不会修改历史金额账单。未配置价格的模型在启用后无法调用。"
      type="info"
      show-icon
      class="rate-notice"
    />
    <ResourceTable
      :columns="columns"
      :rows="cards"
      :loading="loading"
      :scroll-x="'100%'"
      empty-text="尚未配置积分价格"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'price'"
          ><ATag v-if="record.is_free" color="success">明确免费</ATag
          ><span v-else-if="record.billing_mode === 'search_unit'"
            >{{ formatPoints(record.search_points) }} 积分 / 检索单元</span
          >
          <div v-else class="rate-lines">
            <span
              >输入 {{ formatPoints(record.input_points) }} / 输出
              {{ formatPoints(record.output_points) }}</span
            ><span>缓存 {{ formatPoints(record.cache_points) }} 积分 / 百万 Token</span>
          </div></template
        >
        <span v-else-if="column.key === 'created'">{{ creditTime(record.created_at) }}</span>
      </template>
    </ResourceTable>
    <template v-if="canWrite">
      <ADivider orientation="left">新增积分价格版本</ADivider>
      <AForm :name="formName" :model="form" layout="vertical" @submit.prevent="save">
        <AFormItem
          ><ACheckbox :checked="form.is_free" :disabled="saving" @update:checked="setFree"
            >明确设为免费（所有积分单价为零）</ACheckbox
          ></AFormItem
        >
        <div v-if="!searchUnit" class="rate-grid">
          <AFormItem label="输入积分 / 百万 Token" name="input_points"
            ><AInput v-model:value="form.input_points" :disabled="form.is_free || saving"
          /></AFormItem>
          <AFormItem label="输出积分 / 百万 Token" name="output_points"
            ><AInput v-model:value="form.output_points" :disabled="form.is_free || saving"
          /></AFormItem>
          <AFormItem label="缓存积分 / 百万 Token" name="cache_points"
            ><AInput v-model:value="form.cache_points" :disabled="form.is_free || saving"
          /></AFormItem>
        </div>
        <AFormItem v-else label="积分 / 检索单元" name="search_points"
          ><AInput v-model:value="form.search_points" :disabled="form.is_free || saving"
        /></AFormItem>
        <AFormItem label="备注" name="reason"
          ><AInput.TextArea
            v-model:value="form.reason"
            :disabled="saving"
            :rows="2"
            :maxlength="1000"
            placeholder="选填，可说明本次价格调整"
        /></AFormItem>
        <p class="rate-hint">
          输入价格适用于未命中缓存的输入；缓存按缓存价格计算，不会重复计费。新增价格仅影响后续调用。
        </p>
        <div class="rate-submit">
          <AButton type="primary" :loading="saving" @click="save">创建积分价格版本</AButton>
        </div>
      </AForm>
    </template>
  </section>
</template>
<style scoped>
.rate-notice {
  margin-bottom: 16px;
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
}
@media (max-width: 640px) {
  .rate-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
