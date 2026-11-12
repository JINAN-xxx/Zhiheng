<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, onMounted, reactive, ref, useId } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Drawer as ADrawer,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Pagination as APagination,
  Tag as ATag
} from 'ant-design-vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { identity } from '../../identity/public.ts'
import {
  adjustCredits,
  getCreditAccounts,
  getMyCredits,
  setCreditAccountState
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
import { useRouter } from 'vue-router'
const router = useRouter()
const accountName = (account: CreditAccount) => account.display_name.trim() || account.username
const openTokens = (user: CreditAccount) =>
  router.push({
    path: '/usage',
    query: { tab: 'history', userId: user.user_id, userName: accountName(user) }
  })

const formName = `billing-account-action-${useId()}`
const props = defineProps<{ management?: boolean; billingState?: CreditState | null }>()
const emit = defineEmits<{ loading: [value: boolean] }>()
const access = computed(() => creditAccess(identity.value))
const state = computed(() => props.billingState ?? null)
const summary = ref<CreditSummary | null>(null),
  hasData = ref(false)
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
const dialog = ref<'grant' | 'reclaim' | 'account' | null>(null)
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
        : ''
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
        : ''
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
        {
          label: '待结算积分',
          value: summary.value.reserved_points,
          testId: 'reserved-points'
        },
        {
          label: '已消费',
          value: summary.value.used_points,
          testId: 'used-points'
        },
        {
          label: '累计发放',
          value: summary.value.allocated_points,
          testId: 'allocated-points'
        },
        {
          label: '累计回收',
          value: summary.value.reclaimed_points,
          testId: 'reclaimed-points'
        }
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
  emit('loading', false)
})
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  emit('loading', true)
  error.value = ''
  try {
    const [own, result] = await Promise.all([
      !props.management ? getMyCredits(context.signal) : Promise.resolve(null),
      props.management && access.value.read
        ? getCreditAccounts(search.value.trim(), page.value, context.signal)
        : Promise.resolve(null)
    ])
    if (!context.isCurrent()) return
    summary.value = own
    hasData.value = true
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
    if (context.isCurrent()) {
      loading.value = false
      emit('loading', false)
    }
  }
}
function open(kind: NonNullable<typeof dialog.value>, account?: CreditAccount): void {
  if (!access.value.write) return
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
  if (!kind || !account || saving.value || !access.value.write) return
  formError.value = ''
  if (!form.reason.trim()) {
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
          enabled: !account.enabled,
          expected_version: account.version,
          reason
        }
  const signature = JSON.stringify({ kind, user_id: account?.user_id, ...payload })
  if (!retryBody || retrySignature !== signature) {
    retrySignature = signature
    retryBody = { ...payload, client_request_id: createRequestId() }
  }
  const context = mutation.replace()
  saving.value = true
  try {
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
        : ''
}
defineExpose({ reload: load })
function searchAccounts(): void {
  page.value = 1
  void load()
}
</script>
<template>
  <section class="credits-panel" aria-label="平台积分">
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
      v-if="error && hasData"
      :message="`刷新失败，显示上次数据：${error}`"
      type="warning"
      show-icon
      class="credit-notice"
    />
    <WorkspaceState v-if="loading && !hasData" kind="loading" />
    <WorkspaceState v-else-if="error && !hasData" kind="error" :message="error" @retry="load" />
    <template v-else-if="!management && summary">
      <AAlert
        v-if="state?.enabled && !summary.enabled"
        message="积分账户已暂停，无法发起新的积分消费；已有消费仍会结算。"
        type="warning"
        show-icon
        class="credit-notice"
      />
      <AAlert
        v-if="
          state?.enabled &&
          summary.available_points.startsWith('-') &&
          /[1-9]/.test(summary.available_points)
        "
        message="积分已超额，新的积分调用将被阻止。请联系管理员追加积分。"
        type="error"
        show-icon
        class="credit-notice"
      />
      <div class="credit-summary" aria-label="我的积分概览">
        <article v-for="card in summaryCards" :key="card.label" :class="{ primary: card.primary }">
          <span class="credit-summary__label">{{ card.label }}</span>
          <div class="credit-summary__amount">
            <strong :class="card.testId">{{ formatPoints(card.value) }}</strong
            ><small>积分</small>
          </div>
        </article>
      </div>
      <CreditActivity ref="activity" @changed="load" />
    </template>
    <template v-else-if="management && access.read">
      <div class="accounts-heading">
        <AInput.Search
          v-model:value="search"
          placeholder="搜索用户名或姓名"
          aria-label="搜索用户积分账户"
          enter-button="搜索"
          :loading="loading"
          @search="searchAccounts"
        /><span class="accounts-count">共 {{ total }} 个账户</span>
      </div>
      <div class="accounts-mobile">
        <WorkspaceState v-if="!loading && !accounts.length" kind="empty" title="暂无匹配用户" />
        <article
          v-for="account in accounts"
          :key="account.user_id"
          class="account-mobile"
          :aria-label="`${accountName(account)}的积分账户`"
        >
          <header>
            <div class="account-user">
              <strong :title="account.username">{{ accountName(account) }}</strong>
            </div>
            <div>
              <ATag :color="account.enabled ? 'success' : 'warning'">{{
                account.enabled ? '正常' : '已暂停'
              }}</ATag
              ><ATag v-if="!account.is_active">用户已停用</ATag>
            </div>
          </header>
          <dl>
            <div>
              <dt>可用积分</dt>
              <dd class="point-amount">{{ formatPoints(account.available_points) }}</dd>
            </div>
            <div>
              <dt>预占 / 已消费</dt>
              <dd>
                {{ formatPoints(account.reserved_points) }} /
                {{ formatPoints(account.used_points) }}
              </dd>
            </div>
            <div>
              <dt>累计发放 / 回收</dt>
              <dd>
                {{ formatPoints(account.allocated_points) }} /
                {{ formatPoints(account.reclaimed_points) }}
              </dd>
            </div>
          </dl>
          <footer class="account-buttons">
            <AButton type="link" @click="selectedAccount = account">明细</AButton
            ><AButton type="link" @click="openTokens(account)">Token 用量</AButton>
            <template v-if="access.write"
              ><AButton type="link" @click="open('grant', account)">发放</AButton
              ><AButton type="link" @click="open('reclaim', account)">回收</AButton
              ><AButton type="link" :danger="account.enabled" @click="open('account', account)">{{
                account.enabled ? '暂停' : '恢复'
              }}</AButton></template
            >
          </footer>
        </article>
        <APagination
          v-if="total > 10"
          :current="page"
          :total="total"
          :page-size="10"
          :show-size-changer="false"
          size="small"
          @change="
            (value) => {
              page = value
              void load()
            }
          "
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
              <strong :title="record.username">{{ accountName(record) }}</strong>
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
              ><AButton type="link" @click="openTokens(record)">Token 用量</AButton
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
      :title="`${selectedAccount ? accountName(selectedAccount) : ''} · 积分明细`"
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
          ><RefreshButton label="刷新账户明细" :loading="loading" @click="load" />
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
      <p v-if="target">
        账户：<span :title="target.username">{{ accountName(target) }}</span> · 可用
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
        <AFormItem label="操作原因" name="reason" required
          ><AInput.TextArea
            v-model:value="form.reason"
            :disabled="saving"
            :maxlength="1000"
            :rows="3"
            placeholder="填写发放、回收或状态变更的依据"
        /></AFormItem>
      </AForm>
    </AModal>
  </section>
</template>
<style scoped>
.credits-panel {
  min-width: 0;
  padding: 0 28px 28px;
}
.credit-notice {
  margin-bottom: 16px;
}
.credit-summary {
  display: grid;
  grid-template-columns: minmax(220px, 1.3fr) repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}
.credit-summary article {
  padding: 20px;
  min-width: 0;
  background: var(--gray-25);
  border: 0;
  border-radius: 10px;
}
.credit-summary article.primary {
  background: var(--main-50);
}
.credit-summary__label {
  display: block;
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: 10px;
}
.credit-summary__amount {
  display: flex;
  min-width: 0;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 5px;
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
  color: var(--main-color);
}
.credit-summary small {
  display: block;
  color: var(--color-text-secondary);
  font-size: 11px;
}
.accounts-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 0 0 16px;
}
.accounts-count {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin: 0;
}
.accounts-heading :deep(.ant-input-search) {
  width: min(300px, 100%);
  max-width: 300px;
}
.accounts-table {
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container, white);
}
.accounts-mobile {
  display: none;
}
.account-user strong {
  font-weight: 500;
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
.account-detail-summary > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
@media (max-width: 1000px) {
  .credit-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .credit-summary article.primary {
    grid-column: 1 / -1;
  }
}
@media (max-width: 640px) {
  .credits-panel {
    padding: 0 16px 24px;
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
  .credit-summary article.primary strong {
    font-size: 28px;
  }
  .credit-summary__label {
    margin-bottom: 10px;
  }
  .accounts-heading {
    flex-wrap: wrap;
  }
  .accounts-heading :deep(.ant-input-search) {
    max-width: 100%;
  }
  .accounts-table {
    display: none;
  }
  .accounts-mobile {
    display: grid;
    gap: 14px;
  }
  .account-mobile {
    min-width: 0;
    padding: 16px;
    border: 1px solid var(--gray-150);
    border-radius: 12px;
  }
  .account-mobile header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  .account-mobile .account-user {
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .account-mobile dl {
    display: grid;
    gap: 12px;
    margin: 18px 0;
  }
  .account-mobile dl > div {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }
  .account-mobile dt {
    flex-shrink: 0;
    color: var(--color-text-secondary);
    font-size: 12px;
  }
  .account-mobile dd {
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .account-mobile footer {
    flex-wrap: wrap;
    justify-content: flex-end;
    padding-top: 10px;
    border-top: 1px solid var(--gray-150);
  }
}
</style>
