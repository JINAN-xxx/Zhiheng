<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Modal as AModal,
  Pagination as APagination,
  Tag as ATag
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { hasAnyPermission, identity } from '../../identity/public.ts'
import { getInvocationTrace } from '../api/trace.ts'
import type { InvocationTrace } from '../api/trace.ts'
import { creditSource, creditTime, formatPoints, ledgerKind, usageStatus } from '../presentation.ts'
import { formatUsageCount } from '../reconciliation.ts'
import { acceptedAge, executionStatus, reconciliationTarget, tokenStatus } from '../trace.ts'
import type { ReconciliationTarget } from '../trace.ts'
import UsageReconciliationDialog from './UsageReconciliationDialog.vue'

const props = defineProps<{ runId: string | null; reconcile?: boolean }>()
const emit = defineEmits<{ close: []; changed: [] }>()
const trace = ref<InvocationTrace | null>(null),
  loading = ref(false),
  error = ref('')
const editing = ref<ReconciliationTarget | null>(null),
  currentId = ref<string | null>(null)
const requests = new RequestScope()
const invocation = computed(() => trace.value?.billing.invocation)
const hold = computed(() => trace.value?.billing.reservation)
const target = computed(() => (trace.value ? reconciliationTarget(trace.value) : null))
const canReconcile = computed(
  () =>
    !loading.value &&
    !error.value &&
    target.value &&
    hasAnyPermission(identity.value, 'ai:credits:reconcile')
)
watch(
  () => [props.runId, props.reconcile],
  () => {
    requests.replace()
    editing.value = null
    trace.value = null
    currentId.value = props.runId
    if (props.runId) void load(1, props.reconcile)
  },
  { immediate: true }
)
watch(
  () => identity.value,
  () => {
    requests.replace()
    trace.value = null
    editing.value = null
    emit('close')
  }
)
onBeforeUnmount(() => requests.dispose())
async function load(page = 1, reconcile = false) {
  const id = currentId.value
  if (!id) return
  const context = requests.replace()
  loading.value = true
  error.value = ''
  try {
    const value = await getInvocationTrace(id, page, context.signal)
    if (!context.isCurrent()) return
    trace.value = value
    if (reconcile && hasAnyPermission(identity.value, 'ai:credits:reconcile'))
      editing.value = reconciliationTarget(value)
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) {
      error.value = errorMessage(reason)
      trace.value = null
    }
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function follow(id: string) {
  currentId.value = id
  trace.value = null
  editing.value = null
  void load()
}
function close() {
  requests.replace()
  trace.value = null
  emit('close')
}
function reconciled() {
  editing.value = null
  emit('changed')
  close()
}
const money = (amount: string | null, currency: string | null) =>
  amount === null ? '未核算' : `${formatPoints(amount)} ${currency ?? '币种未记录'}`
</script>
<template>
  <AModal
    :open="runId !== null && editing === null"
    title="调用与费用详情"
    :width="780"
    :footer="null"
    @cancel="close"
  >
    <WorkspaceState v-if="loading && !trace" kind="loading" />
    <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="load()" />
    <section v-else-if="trace && invocation" class="invocation-trace" aria-label="调用追溯">
      <dl>
        <div>
          <dt>运行标识</dt>
          <dd>
            <code>{{ invocation.run_id }}</code>
          </dd>
        </div>
        <div>
          <dt>来源 / 模型</dt>
          <dd>
            {{ creditSource(invocation.source) }} / <code>{{ invocation.model_id }}</code>
          </dd>
        </div>
        <div>
          <dt>付费账户</dt>
          <dd>
            <code>{{ invocation.user_id }}</code>
          </dd>
        </div>
        <div>
          <dt>受理时间</dt>
          <dd>{{ creditTime(invocation.created_at) }}</dd>
        </div>
        <div v-if="invocation.status === 'unknown' || hold?.status === 'unknown'">
          <dt>距受理时长</dt>
          <dd>{{ acceptedAge(invocation.created_at, trace.observed_at) }}（非首次待核对时间）</dd>
        </div>
        <div>
          <dt>执行状态</dt>
          <dd>
            {{ trace.execution ? executionStatus(trace.execution.status) : '原生执行记录未关联' }}
          </dd>
        </div>
        <div v-if="trace.execution">
          <dt>执行发起人</dt>
          <dd>
            <code>{{ trace.execution.owner_id }}</code>
          </dd>
        </div>
        <div>
          <dt>Token 计量状态</dt>
          <dd>
            <ATag>{{ tokenStatus(invocation.status) }}</ATag>
          </dd>
        </div>
        <div>
          <dt>输入 / 输出 Token</dt>
          <dd>
            {{ formatUsageCount(invocation.input_tokens) }} /
            {{ formatUsageCount(invocation.output_tokens) }}
          </dd>
        </div>
        <div>
          <dt>缓存 / 推理 Token</dt>
          <dd>
            {{ formatUsageCount(invocation.cache_tokens) }} /
            {{ formatUsageCount(invocation.reasoning_tokens) }}
          </dd>
        </div>
        <div>
          <dt>检索单元</dt>
          <dd>{{ formatUsageCount(invocation.search_units) }}</dd>
        </div>
      </dl>
      <section aria-label="平台积分">
        <h3>平台积分</h3>
        <dl v-if="hold">
          <div>
            <dt>结算状态</dt>
            <dd>{{ hold.failure_waived ? '异常免计' : usageStatus(hold.status) }}</dd>
          </div>
          <div>
            <dt>原始预占</dt>
            <dd>{{ formatPoints(hold.reserved_points) }} 积分</dd>
          </div>
          <div>
            <dt>实际消费</dt>
            <dd>
              {{
                hold.charged_points === null
                  ? '未结算'
                  : `${formatPoints(hold.charged_points)} 积分`
              }}
            </dd>
          </div>
          <div>
            <dt>调用时价格版本</dt>
            <dd>{{ hold.rate_version }}</dd>
          </div>
        </dl>
        <p v-else>
          {{ invocation.points_enforced ? '原积分预占记录未关联' : '本次调用未启用积分计费' }}
        </p>
        <AAlert
          v-if="hold?.failure_waived"
          type="info"
          show-icon
          message="平台已免计本次积分；供应商未知用量仍保留为未知。"
        />
        <AAlert
          v-else-if="
            invocation.points_enforced &&
            hold?.status === 'settled' &&
            invocation.status === 'unknown'
          "
          type="info"
          show-icon
          message="积分已按确认的计费单位结算；缺失的 Token 诊断仍为未知。"
        />
      </section>
      <section aria-label="平台配置货币金额">
        <h3>平台配置货币金额</h3>
        <dl v-if="invocation.rate_card_id">
          <div>
            <dt>原始预估金额</dt>
            <dd>{{ money(invocation.reserved_cost, invocation.currency) }}</dd>
          </div>
          <div>
            <dt>按实际用量核算金额</dt>
            <dd>{{ money(invocation.billable_cost, invocation.currency) }}</dd>
          </div>
          <div>
            <dt>调用时货币费率版本</dt>
            <dd>
              {{ invocation.rate_version ?? '未记录' }} / <code>{{ invocation.rate_card_id }}</code>
            </dd>
          </div>
        </dl>
        <p v-else>本次调用未保存货币费率，金额未核算。</p>
        <p class="trace-hint">
          Token
          是模型计量，积分是平台记账；货币金额依据调用时的平台配置费率计算，不代表已核验的供应商账单。
        </p>
      </section>
      <section v-if="trace.related.length" aria-label="关联调用">
        <h3>关联调用</h3>
        <p v-for="item in trace.related" :key="item.run_id">
          {{ item.relation === 'parent' ? '父对话调用' : '会话摘要调用' }} ·
          {{ tokenStatus(item.status) }}
          <AButton type="link" @click="follow(item.run_id)">{{ item.run_id }}</AButton>
        </p>
      </section>
      <p v-if="trace.related_has_more" class="trace-hint">
        仅展示前 20 条关联摘要；可按运行标识在调用记录中继续查询。
      </p>
      <section aria-label="本次调用积分流水">
        <h3>本次调用积分流水</h3>
        <p v-if="!trace.billing.ledger_total">暂无关联积分流水</p>
        <article v-for="entry in trace.billing.ledger" :key="entry.id" class="trace-ledger">
          <strong>{{ ledgerKind(entry.kind) }}</strong
          ><time>{{ creditTime(entry.created_at) }}</time>
          <span
            >可用变动 {{ formatPoints(entry.available_delta) }} / 预占变动
            {{ formatPoints(entry.reserved_delta) }} 积分</span
          >
          <span
            >变动后可用 {{ formatPoints(entry.available_after) }} / 预占
            {{ formatPoints(entry.reserved_after) }} 积分</span
          >
          <span>{{ entry.reason || '—' }}</span
          ><code>流水 {{ entry.id }}</code>
        </article>
        <APagination
          v-if="trace.billing.ledger_total > trace.billing.ledger_page_size"
          :current="trace.billing.ledger_page"
          :total="trace.billing.ledger_total"
          :page-size="trace.billing.ledger_page_size"
          :show-size-changer="false"
          @change="load($event)"
        />
      </section>
      <footer>
        <AButton @click="close">关闭</AButton
        ><AButton v-if="canReconcile" type="primary" @click="editing = target">核对用量</AButton>
      </footer>
    </section>
  </AModal>
  <UsageReconciliationDialog :target="editing" @close="editing = null" @changed="reconciled" />
</template>
<style scoped>
.invocation-trace {
  display: grid;
  gap: 20px;
  min-width: 0;
}
.invocation-trace dl {
  display: grid;
  gap: 10px;
  margin: 0;
}
.invocation-trace dl > div {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 12px;
}
.invocation-trace dt,
.trace-hint {
  color: var(--color-text-secondary);
}
.invocation-trace dd {
  margin: 0;
}
.invocation-trace code,
.invocation-trace dd,
.trace-ledger,
.invocation-trace .ant-btn {
  overflow-wrap: anywhere;
}
.invocation-trace .ant-btn {
  height: auto;
  white-space: normal;
  max-width: 100%;
}
.invocation-trace h3 {
  font-size: 14px;
  margin: 0 0 12px;
}
.invocation-trace p {
  margin: 8px 0;
}
.trace-hint {
  font-size: 12px;
  line-height: 1.7;
}
.trace-ledger {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--gray-150);
  font-size: 12px;
}
.trace-ledger > span,
.trace-ledger > code {
  grid-column: 1 / -1;
}
.trace-ledger time {
  text-align: right;
}
.invocation-trace footer {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
}
@media (max-width: 640px) {
  .invocation-trace dl > div {
    grid-template-columns: 105px minmax(0, 1fr);
    gap: 8px;
    font-size: 12px;
  }
}
</style>
