<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Pagination as APagination,
  TabPane as ATabPane,
  Tabs as ATabs,
  Tag as ATag
} from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getCreditLedger, getCreditUsage } from '../api/index.ts'
import type { CreditLedgerEntry, CreditUsage } from '../api/index.ts'
import { formatUsageCount } from '../reconciliation.ts'
import { acceptedAge } from '../trace.ts'
import InvocationTraceDialog from './InvocationTraceDialog.vue'
import { creditSource, creditTime, formatPoints, ledgerKind, usageStatus } from '../presentation.ts'

const props = defineProps<{ accountId?: string; canReconcile?: boolean }>()
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
  usageScope = new RequestScope()
const selectedRun = ref<string | null>(null),
  reconcileOnOpen = ref(false)
const usageColumns = computed(() => [
  { title: '时间 / 运行', key: 'run', width: 250 },
  { title: '来源 / 模型', key: 'model', width: 220 },
  { title: '状态', key: 'status', width: 110 },
  { title: '实际计量', key: 'metrics', width: 280 },
  { title: '原始预占（积分）', key: 'reserved', width: 150 },
  { title: '实际消费（积分）', key: 'charged', width: 180 },
  { title: '价格版本', dataIndex: 'rate_version', key: 'rate_version', width: 100 },
  { title: '操作', key: 'actions', width: 150 }
])
const ledgerColumns = [
  { title: '时间 / 类型', key: 'entry', width: 200 },
  { title: '可用变动', key: 'available_delta', width: 150 },
  { title: '预占变动', key: 'reserved_delta', width: 150 },
  { title: '变动后可用 / 预占', key: 'after', width: 200 },
  { title: '原因 / 运行', key: 'reason', width: 260 },
  { title: '操作人', dataIndex: 'operator_id', key: 'operator_id', width: 170 }
]
const chargedLabel = (record: CreditUsage): string =>
  record.failure_waived
    ? '异常免计'
    : record.charged_points === null
      ? record.status === 'unknown'
        ? '待核对，预占保留'
        : '等待结算'
      : formatPoints(record.charged_points)
watch(
  () => props.accountId,
  () => {
    selectedRun.value = null
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
function openTrace(runId: string, reconcile = false) {
  reconcileOnOpen.value = reconcile
  selectedRun.value = runId
}
function openReconcile(row: CreditUsage) {
  openTrace(row.run_id, true)
}
function changed() {
  emit('changed')
  void reload()
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
        <div class="credit-mobile-list">
          <p v-if="!usageLoading && !usage.length" class="credit-mobile-empty">暂无积分消费</p>
          <article v-for="record in usage" :key="record.run_id" class="credit-usage-card">
            <header>
              <strong>{{ creditSource(record.source) }}</strong
              ><ATag :color="record.status === 'settled' ? 'success' : 'warning'">{{
                record.failure_waived ? '异常免计' : usageStatus(record.status)
              }}</ATag>
            </header>
            <time>{{ creditTime(record.created_at) }}</time
            ><small v-if="record.status === 'unknown'"
              >距受理 {{ acceptedAge(record.created_at) }}</small
            >
            <code>{{ record.model_id }}</code>
            <div class="credit-usage-card__metrics">
              <span
                >输入 / 输出 {{ formatUsageCount(record.input_tokens) }} /
                {{ formatUsageCount(record.output_tokens) }}</span
              >
              <span
                >缓存 / 推理 {{ formatUsageCount(record.cache_tokens) }} /
                {{ formatUsageCount(record.reasoning_tokens) }}</span
              >
              <span>检索单元 {{ formatUsageCount(record.search_units) }}</span>
            </div>
            <dl>
              <div>
                <dt>原始预占</dt>
                <dd>{{ formatPoints(record.reserved_points) }} 积分</dd>
              </div>
              <div>
                <dt>实际消费</dt>
                <dd>
                  {{ chargedLabel(record)
                  }}<template v-if="record.charged_points !== null && !record.failure_waived">
                    积分</template
                  >
                </dd>
              </div>
            </dl>
            <footer>
              <AButton type="link" @click="openTrace(record.run_id)">调用详情</AButton
              ><code>{{ record.run_id }}</code
              ><span>价格版本 {{ record.rate_version }}</span
              ><AButton
                v-if="canReconcile && record.status === 'unknown'"
                type="link"
                @click="openReconcile(record)"
                >核对</AButton
              >
            </footer>
          </article>
          <APagination
            v-if="usageTotal > 10"
            :current="usagePage"
            :total="usageTotal"
            :page-size="10"
            :show-size-changer="false"
            size="small"
            @change="
              (page) => {
                usagePage = page
                void loadUsage()
              }
            "
          />
        </div>
        <ResourceTable
          class="credit-desktop-table"
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
              ><small v-if="record.status === 'unknown'"
                >距受理 {{ acceptedAge(record.created_at) }}</small
              ><code :title="record.run_id">{{ record.run_id }}</code>
            </div>
            <div v-else-if="column.key === 'model'" class="cell-lines">
              <span>{{ creditSource(record.source) }}</span
              ><code :title="record.model_id">{{ record.model_id }}</code>
            </div>
            <ATag
              v-else-if="column.key === 'status'"
              :color="record.status === 'settled' ? 'success' : 'warning'"
              >{{ record.failure_waived ? '异常免计' : usageStatus(record.status) }}</ATag
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
            <span v-else-if="column.key === 'charged'">{{ chargedLabel(record) }}</span>
            <div v-else-if="column.key === 'actions'">
              <AButton type="link" @click="openTrace(record.run_id)">调用详情</AButton>
              <AButton
                v-if="canReconcile && record.status === 'unknown'"
                type="link"
                @click="openReconcile(record)"
                >核对</AButton
              >
            </div>
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
        <div class="credit-mobile-list">
          <p v-if="!ledgerLoading && !ledger.length" class="credit-mobile-empty">暂无积分流水</p>
          <article v-for="record in ledger" :key="record.id" class="credit-usage-card">
            <header>
              <strong>{{ ledgerKind(record.kind) }}</strong
              ><time>{{ creditTime(record.created_at) }}</time>
            </header>
            <dl>
              <div>
                <dt>可用变动</dt>
                <dd>{{ formatPoints(record.available_delta) }}</dd>
              </div>
              <div>
                <dt>预占变动</dt>
                <dd>{{ formatPoints(record.reserved_delta) }}</dd>
              </div>
              <div>
                <dt>变动后可用 / 预占</dt>
                <dd>
                  {{ formatPoints(record.available_after) }} /
                  {{ formatPoints(record.reserved_after) }}
                </dd>
              </div>
            </dl>
            <p class="credit-ledger-reason">{{ record.reason || '—' }}</p>
            <AButton v-if="record.run_id" type="link" @click="openTrace(record.run_id)">{{
              record.run_id
            }}</AButton>
            <small>操作人 {{ record.operator_id || '—' }}</small>
          </article>
          <APagination
            v-if="ledgerTotal > 10"
            :current="ledgerPage"
            :total="ledgerTotal"
            :page-size="10"
            :show-size-changer="false"
            size="small"
            @change="
              (page) => {
                ledgerPage = page
                void loadLedger()
              }
            "
          />
        </div>
        <ResourceTable
          class="credit-desktop-table"
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
              ><AButton v-if="record.run_id" type="link" @click="openTrace(record.run_id)">{{
                record.run_id
              }}</AButton>
            </div>
          </template>
        </ResourceTable>
      </ATabPane>
    </ATabs>
    <InvocationTraceDialog
      :run-id="selectedRun"
      :reconcile="reconcileOnOpen"
      @close="selectedRun = null"
      @changed="changed"
    />
  </section>
</template>
<style scoped>
.credit-activity {
  min-width: 0;
  padding: 0 20px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 12px;
  background: var(--bg-container, white);
}
.credit-mobile-list {
  display: none;
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
@media (max-width: 640px) {
  .credit-activity {
    padding: 0 14px 10px;
  }
  .credit-desktop-table {
    display: none;
  }
  .credit-mobile-list {
    display: grid;
    gap: 14px;
  }
  .credit-mobile-empty {
    margin: 0;
    padding: 28px 0;
    color: var(--color-text-secondary);
    text-align: center;
  }
  .credit-usage-card {
    min-width: 0;
    padding: 0 0 16px;
    border-bottom: 1px solid var(--gray-150);
  }
  .credit-usage-card header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
  }
  .credit-usage-card header strong {
    font-size: 13px;
    font-weight: 500;
  }
  .credit-usage-card time,
  .credit-usage-card code,
  .credit-usage-card small {
    display: block;
    color: var(--color-text-secondary);
    font-size: 11px;
    line-height: 1.7;
    overflow-wrap: anywhere;
  }
  .credit-usage-card__metrics {
    display: grid;
    gap: 5px;
    margin-top: 12px;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
  .credit-usage-card dl {
    display: grid;
    gap: 8px;
    margin: 14px 0;
    font-size: 12px;
  }
  .credit-usage-card dl > div {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }
  .credit-usage-card dt {
    color: var(--color-text-secondary);
  }
  .credit-usage-card dd {
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .credit-usage-card footer {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 5px 10px;
    color: var(--color-text-tertiary);
    font-size: 11px;
  }
  .credit-usage-card footer code {
    flex: 1 1 100%;
  }
  .credit-usage-card footer :deep(.ant-btn) {
    margin-left: auto;
  }
  .credit-ledger-reason {
    overflow-wrap: anywhere;
    font-size: 12px;
    line-height: 1.7;
  }
}
</style>
