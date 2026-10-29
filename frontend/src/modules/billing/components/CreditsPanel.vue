<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, useId } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Drawer as ADrawer,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Tag as ATag
} from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity } from '../../identity/public.ts'
import {
  adjustCredits,
  getCreditAccounts,
  getCreditState,
  getMyCredits,
  setCreditAccountState,
  setCreditState
} from '../api/index.ts'
import type {
  CreditAccount,
  CreditAdjustment,
  CreditState,
  CreditSummary,
  StateMutation
} from '../api/index.ts'
import { creditAccess, formatPoints, validPoints } from '../presentation.ts'
import CreditActivity from './CreditActivity.vue'

const formName = `billing-account-action-${useId()}`
const props = defineProps<{ management?: boolean }>()
const access = computed(() => creditAccess(identity.value))
const state = ref<CreditState | null>(null),
  summary = ref<CreditSummary | null>(null)
const accounts = ref<CreditAccount[]>([]),
  total = ref(0),
  page = ref(1),
  search = ref('')
const loading = ref(false),
  error = ref(''),
  success = ref('')
const selectedAccount = ref<CreditAccount | null>(null)
const activity = ref<InstanceType<typeof CreditActivity> | null>(null)
const scope = new RequestScope(),
  mutation = new RequestScope()
const dialog = ref<'grant' | 'reclaim' | 'account' | 'mode' | null>(null)
const target = ref<CreditAccount | null>(null),
  saving = ref(false),
  formError = ref('')
const form = reactive({ points: '', reason: '' })
let retryBody: CreditAdjustment | StateMutation | null = null
let retrySignature = ''
const dialogTitle = computed(() =>
  dialog.value === 'grant'
    ? '发放积分'
    : dialog.value === 'reclaim'
      ? '回收积分'
      : dialog.value === 'account'
        ? target.value?.enabled
          ? '暂停积分账户'
          : '恢复积分账户'
        : state.value?.enabled
          ? '停用积分计费'
          : '启用积分计费'
)
const confirmText = computed(() =>
  dialog.value === 'grant'
    ? '确认发放'
    : dialog.value === 'reclaim'
      ? '确认回收'
      : dialog.value === 'account'
        ? target.value?.enabled
          ? '确认暂停'
          : '确认恢复'
        : state.value?.enabled
          ? '确认停用'
          : '确认启用'
)
const summaryCards = computed(() =>
  summary.value
    ? [
        {
          label: '可用积分',
          value: summary.value.available_points,
          testId: 'available-points',
          primary: true
        },
        { label: '预占中', value: summary.value.reserved_points, testId: 'reserved-points' },
        { label: '已消费', value: summary.value.used_points, testId: 'used-points' },
        { label: '累计发放', value: summary.value.allocated_points, testId: 'allocated-points' },
        { label: '累计回收', value: summary.value.reclaimed_points, testId: 'reclaimed-points' }
      ]
    : []
)
const columns = computed(() => [
  { title: '用户', key: 'user', width: 200 },
  { title: '可用积分', key: 'available', width: 160 },
  { title: '预占 / 已消费', key: 'usage', width: 180 },
  { title: '累计发放 / 回收', key: 'allocated', width: 190 },
  { title: '账户状态', key: 'state', width: 130 },
  { title: '操作', key: 'actions', width: access.value.write ? 250 : 90 }
])
onMounted(() => {
  void load()
})
onBeforeUnmount(() => {
  scope.dispose()
  mutation.dispose()
})
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const [mode, own, result] = await Promise.all([
      getCreditState(context.signal),
      getMyCredits(context.signal),
      props.management && access.value.read
        ? getCreditAccounts(search.value.trim(), page.value, context.signal)
        : Promise.resolve(null)
    ])
    if (!context.isCurrent()) return
    state.value = mode
    summary.value = own
    if (result) {
      accounts.value = result.items
      total.value = result.total
      if (selectedAccount.value) {
        selectedAccount.value =
          result.items.find((row) => row.user_id === selectedAccount.value?.user_id) ??
          selectedAccount.value
      }
    }
    await activity.value?.reload()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function open(kind: NonNullable<typeof dialog.value>, account?: CreditAccount): void {
  if ((kind === 'mode' && !access.value.switchMode) || (kind !== 'mode' && !access.value.write))
    return
  target.value = account ?? null
  dialog.value = kind
  form.points = ''
  form.reason = ''
  formError.value = ''
  retryBody = null
  retrySignature = ''
  success.value = ''
}
function close(): void {
  mutation.replace()
  saving.value = false
  dialog.value = null
}
async function save(): Promise<void> {
  const kind = dialog.value,
    account = target.value
  if (!kind || saving.value || (kind === 'mode' ? !access.value.switchMode : !access.value.write))
    return
  formError.value = ''
  if (kind !== 'mode' && !form.reason.trim()) {
    formError.value = '请填写操作原因'
    return
  }
  if ((kind === 'grant' || kind === 'reclaim') && !validPoints(form.points.trim(), true)) {
    formError.value = '积分须为正十进制数，最多 18 位小数'
    return
  }
  const reason = form.reason.trim()
  const payload =
    kind === 'grant' || kind === 'reclaim'
      ? { kind, points: form.points.trim(), reason }
      : {
          enabled: kind === 'mode' ? !state.value?.enabled : !account?.enabled,
          expected_version: kind === 'mode' ? (state.value?.version ?? 0) : (account?.version ?? 0),
          reason
        }
  const signature = JSON.stringify({ kind, user_id: account?.user_id, ...payload })
  if (!retryBody || retrySignature !== signature) {
    retrySignature = signature
    retryBody = { ...payload, client_request_id: crypto.randomUUID() }
  }
  const context = mutation.replace()
  saving.value = true
  try {
    if (kind === 'mode') {
      state.value = await setCreditState(retryBody as StateMutation, context.signal)
    } else if (account) {
      const result =
        kind === 'account'
          ? await setCreditAccountState(account.user_id, retryBody as StateMutation, context.signal)
          : await adjustCredits(account.user_id, retryBody as CreditAdjustment, context.signal)
      if (!context.isCurrent()) return
      accounts.value = accounts.value.map((row) =>
        row.user_id === account.user_id ? { ...row, ...result } : row
      )
      if (selectedAccount.value?.user_id === account.user_id)
        selectedAccount.value = { ...account, ...result }
    }
    if (!context.isCurrent()) return
    dialog.value = null
    success.value = `${dialogTitleFor(kind, account)}成功`
    await load()
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason))
      formError.value =
        reason instanceof ApiError && reason.status === 409 && reason.code === 'version_conflict'
          ? '操作冲突或版本已变化。请关闭弹窗并刷新最新账户后重试；已确认的操作不会重复执行。'
          : errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
function dialogTitleFor(kind: string, account: CreditAccount | null): string {
  return kind === 'grant'
    ? '发放积分'
    : kind === 'reclaim'
      ? '回收积分'
      : kind === 'account'
        ? account?.enabled
          ? '暂停账户'
          : '恢复账户'
        : '切换积分计费'
}
function searchAccounts(): void {
  page.value = 1
  void load()
}
</script>
<template>
  <section class="credits-panel" aria-label="平台积分">
    <div class="credit-mode">
      <AAlert
        v-if="state"
        :message="state.enabled ? '积分计费已启用' : '积分计费未启用，当前沿用原规则'"
        :description="
          state.enabled
            ? '调用按锁定的积分价格预占和结算；积分长期有效。'
            : '可先准备模型积分价格与用户余额，由超级管理员明确启用。新用户默认零积分。'
        "
        :type="state.enabled ? 'success' : 'info'"
        show-icon
      />
      <div class="credit-actions">
        <AButton v-if="access.switchMode && state" :danger="state.enabled" @click="open('mode')">{{
          state.enabled ? '停用积分计费' : '启用积分计费'
        }}</AButton>
        <AButton :loading="loading" aria-label="刷新积分" @click="load"
          ><ReloadOutlined />刷新</AButton
        >
      </div>
    </div>
    <AAlert
      v-if="success"
      :message="success"
      type="success"
      show-icon
      closable
      class="credit-notice"
      @close="success = ''"
    />
    <AAlert
      v-if="error && summary"
      :message="`刷新失败，显示上次数据：${error}`"
      type="warning"
      show-icon
      class="credit-notice"
    />
    <WorkspaceState v-if="loading && !summary" kind="loading" />
    <WorkspaceState v-else-if="error && !summary" kind="error" :message="error" @retry="load" />
    <template v-else-if="!management && summary">
      <AAlert
        v-if="!summary.enabled"
        message="积分账户已暂停，无法发起新的积分消费；已有消费仍会结算。"
        type="warning"
        show-icon
        class="credit-notice"
      />
      <AAlert
        v-if="summary.available_points.startsWith('-') && /[1-9]/.test(summary.available_points)"
        message="积分已超额，新的积分调用将被阻止。请联系管理员追加积分。"
        type="error"
        show-icon
        class="credit-notice"
      />
      <div class="credit-summary" aria-label="我的积分概览">
        <article v-for="card in summaryCards" :key="card.label" :class="{ primary: card.primary }">
          <span>{{ card.label }}</span
          ><strong :class="card.testId">{{ formatPoints(card.value) }}</strong
          ><small>积分</small>
        </article>
      </div>
      <CreditActivity ref="activity" @changed="load" />
    </template>
    <template v-else-if="management && access.read">
      <div class="accounts-heading">
        <div>
          <h2>用户积分账户</h2>
          <p>手工发放长期有效；回收仅影响可用积分，API Key 共享应用所有者余额。</p>
        </div>
        <AInput.Search
          v-model:value="search"
          placeholder="搜索用户名或姓名"
          aria-label="搜索用户积分账户"
          enter-button="搜索"
          :loading="loading"
          @search="searchAccounts"
        />
      </div>
      <div class="accounts-table">
        <ResourceTable
          :columns="columns"
          :rows="accounts"
          :loading="loading"
          row-key="user_id"
          :pagination="{
            current: page,
            pageSize: 10,
            total,
            showSizeChanger: false,
            onChange: (value: number) => {
              page = value
              void load()
            }
          }"
          empty-text="暂无匹配用户"
        >
          <template #bodyCell="{ column, record }">
            <div v-if="column.key === 'user'" class="account-user">
              <strong>{{ record.display_name || record.username }}</strong
              ><span>{{ record.username }}</span>
            </div>
            <strong v-else-if="column.key === 'available'" class="point-amount">{{
              formatPoints(record.available_points)
            }}</strong>
            <span v-else-if="column.key === 'usage'"
              >{{ formatPoints(record.reserved_points) }} /
              {{ formatPoints(record.used_points) }}</span
            >
            <span v-else-if="column.key === 'allocated'"
              >{{ formatPoints(record.allocated_points) }} /
              {{ formatPoints(record.reclaimed_points) }}</span
            >
            <div v-else-if="column.key === 'state'">
              <ATag :color="record.enabled ? 'success' : 'warning'">{{
                record.enabled ? '正常' : '已暂停'
              }}</ATag
              ><ATag v-if="!record.is_active">用户已停用</ATag>
            </div>
            <div v-else-if="column.key === 'actions'" class="account-buttons">
              <AButton type="link" @click="selectedAccount = record">明细</AButton
              ><template v-if="access.write"
                ><AButton type="link" @click="open('grant', record)">发放</AButton
                ><AButton type="link" @click="open('reclaim', record)">回收</AButton
                ><AButton type="link" :danger="record.enabled" @click="open('account', record)">{{
                  record.enabled ? '暂停' : '恢复'
                }}</AButton></template
              >
            </div>
          </template>
        </ResourceTable>
      </div>
    </template>
    <ADrawer
      :open="selectedAccount !== null"
      :title="`${selectedAccount?.display_name || selectedAccount?.username || ''} · 积分明细`"
      width="min(1100px, 96vw)"
      destroy-on-close
      @close="selectedAccount = null"
    >
      <template v-if="selectedAccount"
        ><div class="account-detail-summary">
          <span
            >可用 <strong>{{ formatPoints(selectedAccount.available_points) }}</strong> 积分</span
          ><span>预占 {{ formatPoints(selectedAccount.reserved_points) }}</span
          ><ATag>{{ selectedAccount.enabled ? '账户正常' : '账户已暂停' }}</ATag
          ><AButton aria-label="刷新账户明细" @click="load">刷新</AButton>
        </div>
        <CreditActivity
          :account-id="selectedAccount.user_id"
          :can-write="access.write"
          @changed="load"
      /></template>
    </ADrawer>
    <AModal
      :open="dialog !== null"
      :title="dialogTitle"
      :confirm-loading="saving"
      :ok-text="confirmText"
      cancel-text="取消"
      :mask-closable="false"
      @ok="save"
      @cancel="close"
    >
      <AAlert v-if="formError" :message="formError" type="error" show-icon class="credit-notice" />
      <AAlert
        v-if="dialog === 'mode'"
        :message="
          state?.enabled
            ? '停用后新调用恢复原有规则，已预占的积分调用继续按原价格结算。'
            : '请先配置模型积分价格并准备用户余额。启用后，未配置价格或余额不足的调用将被拒绝。'
        "
        type="warning"
        show-icon
        class="credit-notice"
      />
      <p v-if="target">
        账户：{{ target.display_name || target.username }}（{{ target.username }}）· 可用
        {{ formatPoints(target.available_points) }} 积分
      </p>
      <AAlert
        v-if="dialog === 'reclaim'"
        message="仅回收未使用且未预占的可用积分；已消费与预占部分不会被回收。"
        type="info"
        show-icon
        class="credit-notice"
      />
      <AAlert
        v-if="dialog === 'account'"
        :message="
          target?.enabled
            ? '暂停将阻止新的积分调用，已发生的消费仍会结算。'
            : '恢复后可继续使用此账户的可用积分。'
        "
        type="info"
        show-icon
        class="credit-notice"
      />
      <AForm :name="formName" :model="form" layout="vertical">
        <AFormItem
          v-if="dialog === 'grant' || dialog === 'reclaim'"
          label="积分数量"
          name="points"
          required
          ><AInput
            v-model:value="form.points"
            :disabled="saving"
            placeholder="请输入正数，最多 18 位小数"
        /></AFormItem>
        <AFormItem
          :label="dialog === 'mode' ? '备注' : '操作原因'"
          name="reason"
          :required="dialog !== 'mode'"
          ><AInput.TextArea
            v-model:value="form.reason"
            :disabled="saving"
            :maxlength="1000"
            :rows="3"
            :placeholder="dialog === 'mode' ? '选填' : '填写发放、回收或状态变更的依据'"
        /></AFormItem>
      </AForm>
      <p class="credit-footnote">操作会留存审计记录；相同请求重试不会重复发放或回收。</p>
    </AModal>
  </section>
</template>
<style scoped>
.credits-panel {
  min-width: 0;
  padding: 0 24px 28px;
}
.credit-mode {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 22px;
}
.credit-mode > :first-child {
  flex: 1;
}
.credit-actions {
  display: flex;
  gap: 10px;
  padding-top: 3px;
}
.credit-notice {
  margin-bottom: 16px;
}
.credit-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}
.credit-summary article {
  padding: 20px;
  min-width: 0;
  background: var(--bg-container, white);
  border: 1px solid var(--gray-150);
  border-radius: 10px;
}
.credit-summary article.primary {
  border-color: #d5d9fa;
  background: linear-gradient(135deg, #f5f7ff, white);
}
.credit-summary span {
  display: block;
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: 12px;
}
.credit-summary strong {
  display: block;
  overflow-wrap: anywhere;
  font-size: 25px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.credit-summary .primary strong,
.point-amount {
  color: #5365c9;
}
.credit-summary small {
  display: block;
  margin-top: 6px;
  color: var(--color-text-secondary);
}
.accounts-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 4px 0 18px;
}
.accounts-heading h2 {
  margin: 0 0 7px;
  font-size: 17px;
  font-weight: 500;
}
.accounts-heading p,
.credit-footnote {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 0;
}
.accounts-heading :deep(.ant-input-search) {
  max-width: 300px;
}
.accounts-table {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container, white);
}
.account-user {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.account-user strong {
  font-weight: 500;
}
.account-user span {
  font-size: 12px;
  color: var(--color-text-secondary);
}
.account-buttons {
  display: flex;
}
.account-buttons :deep(.ant-btn) {
  padding: 4px 7px;
}
.account-detail-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 0 0 20px;
  align-items: center;
}
@media (max-width: 1000px) {
  .credit-summary {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 640px) {
  .credits-panel {
    padding: 0 14px 20px;
  }
  .credit-mode {
    flex-direction: column;
  }
  .credit-mode > :first-child {
    width: 100%;
  }
  .credit-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
  }
  .credit-summary article {
    padding: 14px;
  }
  .credit-summary strong {
    font-size: 22px;
  }
  .accounts-heading {
    flex-direction: column;
    align-items: stretch;
  }
  .accounts-heading :deep(.ant-input-search) {
    max-width: 100%;
  }
}
</style>
