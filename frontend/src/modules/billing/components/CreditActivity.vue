<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, useId, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal as AModal,
  TabPane as ATabPane,
  Tabs as ATabs,
  Tag as ATag
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getCreditLedger, getCreditUsage, reconcileCreditUsage } from '../api/index.ts'
import type { CreditLedgerEntry, CreditReconciliation, CreditUsage } from '../api/index.ts'
import {
  formatUsageCount,
  hasConfirmedUsage,
  reconciliationDraft,
  reconciliationPayload
} from '../reconciliation.ts'
import type { ReconciliationDraft } from '../reconciliation.ts'
import { creditSource, creditTime, formatPoints, ledgerKind, usageStatus } from '../presentation.ts'

const formName = `billing-reconciliation-${useId()}`
const props = defineProps<{ accountId?: string; canWrite?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const ledger = ref<CreditLedgerEntry[]>([]),
  usage = ref<CreditUsage[]>([])
const ledgerPage = ref(1),
  usagePage = ref(1),
  ledgerTotal = ref(0),
  usageTotal = ref(0)
const ledgerLoading = ref(false),
  usageLoading = ref(false),
  ledgerError = ref(''),
  usageError = ref('')
const ledgerScope = new RequestScope(),
  usageScope = new RequestScope(),
  mutation = new RequestScope()
const selected = ref<CreditUsage | null>(null),
  saving = ref(false),
  formError = ref('')
const form = reactive<ReconciliationDraft>({
  reason: '',
  input_tokens: null,
  output_tokens: null,
  cache_tokens: null,
  search_units: null,
  rejected_before_execution: false
})
let retryBody: CreditReconciliation | null = null
let retrySignature = ''
const usageColumns = computed(() => [
  { title: '时间 / 运行', key: 'run', width: 250 },
  { title: '来源 / 模型', key: 'model', width: 220 },
  { title: '状态', key: 'status', width: 110 },
  { title: '实际计量', key: 'metrics', width: 280 },
  { title: '原始预占（积分）', key: 'reserved', width: 150 },
  { title: '实际消费（积分）', key: 'charged', width: 180 },
  { title: '价格版本', dataIndex: 'rate_version', key: 'rate_version', width: 100 },
  ...(props.canWrite ? [{ title: '操作', key: 'actions', width: 90 }] : [])
])
const ledgerColumns = [
  { title: '时间 / 类型', key: 'entry', width: 200 },
  { title: '可用变动', key: 'available_delta', width: 150 },
  { title: '预占变动', key: 'reserved_delta', width: 150 },
  { title: '变动后可用 / 预占', key: 'after', width: 200 },
  { title: '原因 / 运行', key: 'reason', width: 260 },
  { title: '操作人', dataIndex: 'operator_id', key: 'operator_id', width: 170 }
]
watch(
  () => props.accountId,
  () => {
    selected.value = null
    mutation.replace()
    saving.value = false
    ledgerPage.value = 1
    usagePage.value = 1
    ledger.value = []
    usage.value = []
    void reload()
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  ledgerScope.dispose()
  usageScope.dispose()
  mutation.dispose()
})
async function loadLedger(): Promise<void> {
  const context = ledgerScope.replace()
  ledgerLoading.value = true
  ledgerError.value = ''
  try {
    const result = await getCreditLedger(props.accountId, ledgerPage.value, context.signal)
    if (context.isCurrent()) {
      ledger.value = result.items
      ledgerTotal.value = result.total
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) ledgerError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) ledgerLoading.value = false
  }
}
async function loadUsage(): Promise<void> {
  const context = usageScope.replace()
  usageLoading.value = true
  usageError.value = ''
  try {
    const result = await getCreditUsage(props.accountId, usagePage.value, context.signal)
    if (context.isCurrent()) {
      usage.value = result.items
      usageTotal.value = result.total
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) usageError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) usageLoading.value = false
  }
}
async function reload(): Promise<void> {
  await Promise.all([loadLedger(), loadUsage()])
}
defineExpose({ reload })
function openReconcile(row: CreditUsage): void {
  selected.value = row
  formError.value = ''
  retryBody = null
  retrySignature = ''
  Object.assign(form, reconciliationDraft(row))
}
function close(): void {
  mutation.replace()
  saving.value = false
  selected.value = null
}
function setCount(
  field: 'input_tokens' | 'output_tokens' | 'cache_tokens' | 'search_units',
  value: unknown
): void {
  form[field] =
    value === null || value === undefined || value === ''
      ? null
      : typeof value === 'number'
        ? value
        : Number.NaN
}
async function reconcile(): Promise<void> {
  const row = selected.value
  if (!row || !props.canWrite || saving.value) return
  formError.value = ''
  let payload: Omit<CreditReconciliation, 'client_request_id'>
  try {
    payload = reconciliationPayload(row, form)
  } catch (reason) {
    formError.value = errorMessage(reason)
    return
  }
  const signature = JSON.stringify(payload)
  if (!retryBody || signature !== retrySignature) {
    retrySignature = signature
    retryBody = { ...payload, client_request_id: crypto.randomUUID() }
  }
  const context = mutation.replace()
  saving.value = true
  try {
    await reconcileCreditUsage(row.run_id, retryBody, context.signal)
    if (!context.isCurrent()) return
    selected.value = null
    emit('changed')
    await reload()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      formError.value =
        reason instanceof ApiError && reason.status === 409 && reason.code === 'version_conflict'
          ? '记录版本已变化，请关闭弹窗并刷新后重新核对'
          : errorMessage(reason)
    }
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
</script>
<template>
  <section class="credit-activity" aria-label="积分明细">
    <ATabs>
      <ATabPane key="usage" tab="积分消费">
        <AAlert
          v-if="usageError"
          :message="usageError"
          type="warning"
          show-icon
          class="activity-alert"
        />
        <ResourceTable
          :columns="usageColumns"
          :rows="usage"
          :loading="usageLoading"
          row-key="run_id"
          :pagination="{
            current: usagePage,
            pageSize: 10,
            total: usageTotal,
            showSizeChanger: false,
            onChange: (page: number) => {
              usagePage = page
              void loadUsage()
            }
          }"
          empty-text="暂无积分消费"
        >
          <template #bodyCell="{ column, record }">
            <div v-if="column.key === 'run'" class="cell-lines">
              <span>{{ creditTime(record.created_at) }}</span
              ><code :title="record.run_id">{{ record.run_id }}</code>
            </div>
            <div v-else-if="column.key === 'model'" class="cell-lines">
              <span>{{ creditSource(record.source) }}</span
              ><code :title="record.model_id">{{ record.model_id }}</code>
            </div>
            <ATag
              v-else-if="column.key === 'status'"
              :color="record.status === 'settled' ? 'success' : 'warning'"
              >{{ usageStatus(record.status) }}</ATag
            >
            <div v-else-if="column.key === 'metrics'" class="cell-lines">
              <span
                >输入 {{ formatUsageCount(record.input_tokens) }} / 输出
                {{ formatUsageCount(record.output_tokens) }}</span
              >
              <span
                >缓存 {{ formatUsageCount(record.cache_tokens) }} / 推理
                {{ formatUsageCount(record.reasoning_tokens) }}</span
              >
              <span>检索单元 {{ formatUsageCount(record.search_units) }}</span>
            </div>
            <span v-else-if="column.key === 'reserved'">{{
              formatPoints(record.reserved_points)
            }}</span>
            <span v-else-if="column.key === 'charged'">{{
              record.charged_points === null
                ? record.status === 'unknown'
                  ? '待核对，预占保留'
                  : '等待结算'
                : formatPoints(record.charged_points)
            }}</span>
            <AButton
              v-else-if="column.key === 'actions' && record.status === 'unknown'"
              type="link"
              @click="openReconcile(record)"
              >核对</AButton
            >
          </template>
        </ResourceTable>
        <p class="activity-hint">
          未知用量保留预占，核实实际用量后结算；每条消费保存调用时的积分价格版本。
        </p>
      </ATabPane>
      <ATabPane key="ledger" tab="积分流水">
        <AAlert
          v-if="ledgerError"
          :message="ledgerError"
          type="warning"
          show-icon
          class="activity-alert"
        />
        <ResourceTable
          :columns="ledgerColumns"
          :rows="ledger"
          :loading="ledgerLoading"
          :pagination="{
            current: ledgerPage,
            pageSize: 10,
            total: ledgerTotal,
            showSizeChanger: false,
            onChange: (page: number) => {
              ledgerPage = page
              void loadLedger()
            }
          }"
          empty-text="暂无积分流水"
        >
          <template #bodyCell="{ column, record }">
            <div v-if="column.key === 'entry'" class="cell-lines">
              <span>{{ creditTime(record.created_at) }}</span
              ><ATag>{{ ledgerKind(record.kind) }}</ATag>
            </div>
            <span v-else-if="column.key === 'available_delta'">{{
              formatPoints(record.available_delta)
            }}</span>
            <span v-else-if="column.key === 'reserved_delta'">{{
              formatPoints(record.reserved_delta)
            }}</span>
            <span v-else-if="column.key === 'after'"
              >{{ formatPoints(record.available_after) }} /
              {{ formatPoints(record.reserved_after) }}</span
            >
            <div v-else-if="column.key === 'reason'" class="cell-lines">
              <span>{{ record.reason || '—' }}</span
              ><code v-if="record.run_id">{{ record.run_id }}</code>
            </div>
          </template>
        </ResourceTable>
      </ATabPane>
    </ATabs>
    <AModal
      :open="selected !== null"
      title="核对积分消费"
      :confirm-loading="saving"
      :mask-closable="false"
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
          <AFormItem v-else label="实际检索单元" name="search_units" required
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
  </section>
</template>
<style scoped>
.credit-activity {
  min-width: 0;
  padding: 0 20px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container, white);
}
.cell-lines {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}
.cell-lines code {
  max-width: 230px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.activity-hint {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 14px 0 8px;
}
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
@media (max-width: 640px) {
  .credit-activity {
    padding: 0 10px 8px;
  }
}
</style>
