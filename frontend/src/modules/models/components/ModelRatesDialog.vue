<!-- Adapted from original PlatformModelsPanel.vue rate dialog, MIT licensed. -->
<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Divider as ADivider,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Tag as ATag
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createRateCard, getRateCards } from '../api/index.ts'
import type { ChatModel, RateCard, RateInput } from '../api/index.ts'
const props = defineProps<{ model: ChatModel | null }>()
const emit = defineEmits<{ close: [] }>()
const cards = ref<RateCard[]>([]),
  loading = ref(false),
  saving = ref(false),
  error = ref('')
const scope = new RequestScope(),
  mutation = new RequestScope()
const form = reactive<RateInput>({
  billing_mode: 'token',
  input_price: '0',
  output_price: '0',
  cache_price: '0',
  search_price: '0',
  currency: 'USD'
})
const columns = [
  { title: '版本', dataIndex: 'version', key: 'version', width: 80 },
  { title: '价格', key: 'price' },
  { title: '币种', dataIndex: 'currency', key: 'currency', width: 80 }
]
watch(
  () => props.model?.id,
  async (id) => {
    const context = scope.replace()
    mutation.replace()
    saving.value = false
    cards.value = []
    error.value = ''
    Object.assign(form, {
      billing_mode: props.model?.model_type === 'rerank' ? 'search_unit' : 'token',
      input_price: '0',
      output_price: '0',
      cache_price: '0',
      search_price: '0',
      currency: 'USD'
    })
    if (!id) return
    loading.value = true
    try {
      const result = await getRateCards(id, context.signal)
      if (context.isCurrent()) cards.value = result
    } catch (reason) {
      if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
    } finally {
      if (context.isCurrent()) loading.value = false
    }
  }
)
onBeforeUnmount(() => {
  scope.dispose()
  mutation.dispose()
})
function close(): void {
  scope.replace()
  mutation.replace()
  saving.value = false
  emit('close')
}
async function save(): Promise<void> {
  const id = props.model?.id
  if (!id || saving.value) return
  error.value = ''
  const prices = [form.input_price, form.output_price, form.cache_price, form.search_price]
  if (prices.some((value) => !/^(0|[1-9]\d*)(\.\d{1,12})?$/.test(value.trim()))) {
    error.value = '单价须为非负十进制数，最多 12 位小数'
    return
  }
  const context = mutation.replace()
  saving.value = true
  try {
    const result = await createRateCard(
      id,
      { ...form, currency: form.currency.trim().toUpperCase() },
      context.signal
    )
    if (!context.isCurrent() || props.model?.id !== id) return
    const refresh = scope.replace()
    loading.value = true
    cards.value = [result, ...cards.value.filter((card) => card.id !== result.id)]
    try {
      const history = await getRateCards(id, refresh.signal)
      if (refresh.isCurrent()) {
        const versions = new Map([...history, ...cards.value].map((card) => [card.id, card]))
        cards.value = [...versions.values()].sort((left, right) => right.version - left.version)
      }
    } catch (reason) {
      if (refresh.isCurrent() && !isAborted(reason))
        error.value = `价格版本已创建，历史刷新失败：${errorMessage(reason)}`
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
  <AModal
    :open="model !== null"
    title="费率配置"
    width="min(680px, 94vw)"
    destroy-on-close
    @cancel="close"
  >
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <div class="rate-context">
      <strong>{{ model?.name }}</strong
      ><ATag>{{
        model?.model_type === 'rerank'
          ? '重排序'
          : model?.model_type === 'embedding'
            ? '向量'
            : '对话'
      }}</ATag
      ><span class="muted">{{
        form.billing_mode === 'search_unit' ? '按检索单元计费' : '按令牌计费'
      }}</span>
    </div>
    <h3 class="rate-history-title">历史费率版本</h3>
    <ResourceTable :columns="columns" :rows="cards" :loading="loading" :scroll-x="'100%'">
      <template #bodyCell="{ column, record }"
        ><template v-if="column.key === 'price'"
          ><span v-if="record.billing_mode === 'search_unit'">{{ record.search_price }} / 单元</span
          ><span v-else
            >输入 {{ record.input_price }} / 输出 {{ record.output_price }} / 缓存
            {{ record.cache_price }} · 每 Token</span
          ></template
        ><template v-else-if="column.key === 'version'">{{ record.version }}</template
        ><template v-else-if="column.key === 'currency'">{{ record.currency }}</template></template
      >
    </ResourceTable>
    <ADivider orientation="left">新增价格版本</ADivider>
    <AForm :model="form" layout="vertical" @submit.prevent>
      <div v-if="form.billing_mode === 'token'" class="rate-grid">
        <AFormItem label="输入令牌单价" name="input_price"
          ><AInput v-model:value="form.input_price"
        /></AFormItem>
        <AFormItem label="输出令牌单价" name="output_price"
          ><AInput v-model:value="form.output_price"
        /></AFormItem>
        <AFormItem label="缓存令牌单价（可选）" name="cache_price"
          ><AInput v-model:value="form.cache_price"
        /></AFormItem>
      </div>
      <AFormItem v-else label="检索单元单价" name="search_price"
        ><AInput v-model:value="form.search_price"
      /></AFormItem>
      <AFormItem label="币种" name="currency"
        ><AInput v-model:value="form.currency" :maxlength="3"
      /></AFormItem>
    </AForm>
    <template #footer
      ><AButton @click="close">关闭</AButton
      ><AButton type="primary" :loading="saving" @click="save">创建价格版本</AButton></template
    >
  </AModal>
</template>
<style scoped lang="scss">
.rate-context {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 0 16px;
  flex-wrap: wrap;
}
.rate-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.rate-context strong {
  font-size: 16px;
  font-weight: 500;
}
.rate-history-title {
  margin: 20px 0 12px;
  font-size: 14px;
  font-weight: 500;
}
@media (max-width: 640px) {
  .rate-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
}
</style>
