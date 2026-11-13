<script setup lang="ts">
import { createRequestId } from '../../../shared/requestId.ts'
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Checkbox as ACheckbox,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  InputNumber as AInputNumber,
  Modal as AModal,
  Pagination as APagination,
  Select as ASelect,
  Tag as ATag
} from 'ant-design-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { identity, hasAnyPermission } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getUsage, reconcileTokenUsage } from '../api/index.ts'
import type { UsageRecord, UsageSummary, TokenReconciliation } from '../api/index.ts'
const props = defineProps<{ accountId?: string | undefined; accountName?: string | undefined }>()
const canWrite = computed(() => hasAnyPermission(identity.value, 'ai:credits:write'))
const summary = ref<UsageSummary | null>(null),
  error = ref(''),
  loading = ref(false),
  page = ref(1)
const source = ref(''),
  status = ref('')
const scope = new RequestScope(),
  writes = new RequestScope()
const sourceNames: Record<string, string> = {
  chat: '对话',
  chat_summary: '会话摘要',
  connection_test: '模型测试',
  public: '开发接入',
  agent_preview: '助手调试',
  agent_chat: '助手对话',
  agent_invocation: '智能体 API',
  work_turn: '工作执行',
  knowledge_embedding: '知识索引',
  knowledge_retrieval: '知识检索',
  knowledge_graph: '知识图谱'
}
const sourceOptions = [
  { value: '', label: '全部类型' },
  ...Object.entries(sourceNames).map(([value, label]) => ({ value, label }))
]
const statusNames: Record<string, string> = {
  known: '已核算',
  unknown: '待核对',
  pending: '待核算'
}
const statusOptions = [
  { value: '', label: '全部状态' },
  ...Object.entries(statusNames).map(([value, label]) => ({ value, label }))
]
const columns = [
  { title: '时间', key: 'created_at', width: 180 },
  { title: '调用类型', key: 'source', width: 120 },
  { title: '运行', key: 'run', width: 170 },
  { title: '输入 / 输出 Token', key: 'tokens' },
  { title: '状态', key: 'status', width: 100 },
  { title: '操作', key: 'actions', width: 100 }
]
const count = (value: number | null) => (value === null ? '待核对' : value.toLocaleString())
async function load() {
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const value = await getUsage(ticket.signal, {
      page: page.value,
      pageSize: 20,
      source: source.value,
      status: status.value,
      userId: props.accountId
    })
    if (ticket.isCurrent()) summary.value = value
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
watch(
  () => [props.accountId, identity.value?.id],
  () => {
    summary.value = null
    page.value = 1
    void load()
  },
  { immediate: true }
)
watch([source, status], () => {
  page.value = 1
  void load()
})
function changePage(value: number) {
  page.value = value
  void load()
}
const dialog = ref<'reconcile' | null>(null),
  current = ref<UsageRecord | null>(null),
  saving = ref(false),
  formError = ref('')
const form = reactive({
  reason: '',
  input: null as number | null,
  output: null as number | null,
  cache: null as number | null,
  search: null as number | null,
  rejected: false
})
let retrySignature = '',
  retryBody: TokenReconciliation | null = null
watch(
  () => [props.accountId, identity.value],
  () => {
    writes.replace()
    dialog.value = null
    current.value = null
    saving.value = false
    retryBody = null
  }
)
function openReconcile(row: UsageRecord) {
  if (!canWrite.value || row.points_enforced || row.status !== 'unknown' || saving.value) return
  Object.assign(form, {
    reason: '',
    input: row.input_tokens,
    output: row.output_tokens,
    cache: null,
    search: null,
    rejected: false
  })
  current.value = row
  formError.value = ''
  retryBody = null
  dialog.value = 'reconcile'
}
async function submit() {
  if (!canWrite.value || saving.value || !summary.value || !dialog.value) return
  formError.value = ''
  const reason = form.reason.trim()
  if (!reason) {
    formError.value = '请填写核对依据'
    return
  }
  const record = current.value
  if (
    dialog.value === 'reconcile' &&
    !form.rejected &&
    (!Number.isSafeInteger(form.input) ||
      !Number.isSafeInteger(form.output) ||
      form.input! < 0 ||
      form.output! < 0)
  ) {
    formError.value = '请输入完整的实际输入、输出 Token'
    return
  }
  const payload = {
    expected_version: record!.version,
    reason,
    rejected_before_execution: form.rejected,
    input_tokens: form.rejected ? null : form.input,
    output_tokens: form.rejected ? null : form.output,
    cache_tokens: form.rejected ? null : form.cache,
    search_units: form.rejected ? null : form.search
  }
  const signature = JSON.stringify([dialog.value, props.accountId, record?.run_id, payload])
  if (!retryBody || signature !== retrySignature) {
    retrySignature = signature
    retryBody = { ...payload, client_request_id: createRequestId() }
  }
  const ticket = writes.replace()
  saving.value = true
  try {
    await reconcileTokenUsage(record!.run_id, retryBody, ticket.signal)
    if (ticket.isCurrent()) {
      dialog.value = null
      retryBody = null
      await load()
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      formError.value = errorMessage(reason)
      if (reason instanceof ApiError && reason.status === 409) await load()
    }
  } finally {
    if (ticket.isCurrent()) saving.value = false
  }
}
onBeforeUnmount(() => {
  scope.dispose()
  writes.dispose()
})
</script>
<template>
  <section class="token-ledger" aria-label="Token 用量记录">
    <header class="ledger-heading">
      <div class="ledger-title">
        <h2>
          {{
            accountName
              ? `${accountName}的 Token 用量`
              : accountId
                ? '用户 Token 用量'
                : '我的 Token 用量'
          }}
        </h2>
        <section v-if="summary" class="token-summary" aria-label="Token 用量统计">
          <div class="token-total">
            <span>累计使用 Token</span>
            <strong>{{ count(summary.used_tokens) }}</strong>
          </div>
        </section>
      </div>
      <div class="ledger-actions">
        <RefreshButton label="刷新用量记录" :loading="loading" @click="load" />
      </div>
    </header>
    <div class="ledger-filters">
      <ASelect
        :value="source"
        :options="sourceOptions"
        aria-label="调用类型"
        @update:value="source = typeof $event === 'string' ? $event : ''"
      /><ASelect
        :value="status"
        :options="statusOptions"
        aria-label="调用状态"
        @update:value="status = typeof $event === 'string' ? $event : ''"
      /><span>共 {{ summary?.total ?? 0 }} 条</span>
    </div>
    <AAlert v-if="error && summary" :message="error" type="warning" show-icon />
    <WorkspaceState v-if="loading && !summary" kind="loading" />
    <WorkspaceState v-else-if="error && !summary" kind="error" :message="error" @retry="load" />
    <template v-else>
      <div class="token-desktop-records">
        <ResourceTable
          :columns="columns"
          :rows="summary?.records ?? []"
          row-key="id"
          :loading="loading"
          empty-text="暂无符合条件的调用记录"
        >
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'created_at'">{{
              new Date(record.created_at).toLocaleString('zh-CN')
            }}</span>
            <span v-else-if="column.key === 'source'">{{
              sourceNames[record.source] ?? record.source
            }}</span>
            <code v-else-if="column.key === 'run'" class="token-run-id" :title="record.run_id">{{
              record.run_id
            }}</code>
            <span v-else-if="column.key === 'tokens'" :title="record.run_id"
              >{{ count(record.input_tokens) }} / {{ count(record.output_tokens) }}</span
            >
            <ATag v-else-if="column.key === 'status'">{{
              statusNames[record.status] ?? record.status
            }}</ATag>
            <AButton
              v-else-if="
                column.key === 'actions' &&
                canWrite &&
                record.status === 'unknown' &&
                !record.points_enforced
              "
              type="link"
              @click="openReconcile(record)"
              >核对用量</AButton
            >
          </template>
        </ResourceTable>
      </div>
      <div class="token-mobile-records">
        <article v-for="record in summary?.records ?? []" :key="record.id">
          <header>
            <time>{{ new Date(record.created_at).toLocaleString('zh-CN') }}</time
            ><ATag>{{ statusNames[record.status] ?? record.status }}</ATag>
          </header>
          <strong>{{ sourceNames[record.source] ?? record.source }}</strong>
          <code class="token-run-id" :title="record.run_id">{{ record.run_id }}</code>
          <p>
            输入 {{ count(record.input_tokens) }} / 输出 {{ count(record.output_tokens) }} Token
          </p>
          <AButton
            v-if="canWrite && record.status === 'unknown' && !record.points_enforced"
            type="link"
            @click="openReconcile(record)"
            >核对用量</AButton
          >
        </article>
        <WorkspaceState
          v-if="!loading && !summary?.records.length"
          kind="empty"
          title="暂无符合条件的调用记录"
        />
      </div>
      <APagination
        v-if="summary && summary.total > 20"
        :current="page"
        :page-size="20"
        :total="summary.total"
        :show-size-changer="false"
        @change="changePage"
      />
    </template>
    <AModal
      :open="dialog !== null"
      title="核对未知 Token 用量"
      :confirm-loading="saving"
      :mask-closable="!saving"
      :closable="!saving"
      :cancel-button-props="{ disabled: saving }"
      @cancel="dialog = null"
      @ok="submit"
    >
      <AAlert v-if="formError" :message="formError" type="error" show-icon />
      <AForm layout="vertical">
        <AFormItem
          ><ACheckbox v-model:checked="form.rejected" :disabled="saving"
            >已核实在执行前被拒绝，没有发生消耗</ACheckbox
          ></AFormItem
        >
        <template v-if="!form.rejected">
          <AFormItem label="实际输入 Token"
            ><AInputNumber
              aria-label="实际输入 Token"
              :value="form.input ?? ''"
              :min="0"
              :precision="0"
              :disabled="saving || current?.input_tokens !== null"
              @update:value="form.input = typeof $event === 'number' ? $event : null"
          /></AFormItem>
          <AFormItem label="实际输出 Token"
            ><AInputNumber
              aria-label="实际输出 Token"
              :value="form.output ?? ''"
              :min="0"
              :precision="0"
              :disabled="saving || current?.output_tokens !== null"
              @update:value="form.output = typeof $event === 'number' ? $event : null"
          /></AFormItem>
          <AFormItem label="缓存 Token（如有）"
            ><AInputNumber
              aria-label="缓存 Token"
              :value="form.cache ?? ''"
              :min="0"
              :precision="0"
              :disabled="saving"
              @update:value="form.cache = typeof $event === 'number' ? $event : null"
          /></AFormItem>
          <AFormItem label="检索单元（重排调用需填写）"
            ><AInputNumber
              aria-label="检索单元"
              :value="form.search ?? ''"
              :min="0"
              :precision="0"
              :disabled="saving"
              @update:value="form.search = typeof $event === 'number' ? $event : null"
          /></AFormItem>
        </template>
        <AFormItem label="依据" required
          ><AInput.TextArea
            v-model:value="form.reason"
            aria-label="核对依据"
            :maxlength="1000"
            :disabled="saving"
        /></AFormItem>
      </AForm>
    </AModal>
  </section>
</template>
<style scoped>
.token-ledger {
  padding: 0 28px 28px;
  display: grid;
  gap: 20px;
  min-width: 0;
}
.ledger-heading,
.ledger-actions,
.ledger-filters {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}
.ledger-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px 28px;
  min-width: 0;
}
.ledger-heading h2 {
  font-size: 19px;
  margin: 0;
  overflow-wrap: anywhere;
}
.ledger-filters > span,
.token-total > span {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.token-total {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 6px 10px;
  min-width: 0;
}
.token-total strong {
  min-width: 0;
  font-size: 24px;
  line-height: 1.2;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.ledger-filters {
  justify-content: flex-start;
  flex-wrap: wrap;
}
.ledger-filters :deep(.ant-select) {
  min-width: 150px;
}
.ledger-filters > span {
  margin-left: auto;
}
.ant-pagination {
  justify-self: end;
}
@media (max-width: 680px) {
  .token-ledger {
    padding: 0 16px 20px;
  }
  .ledger-heading {
    align-items: flex-start;
  }
  .ledger-title {
    flex: 1;
    gap: 8px;
  }
  .ledger-actions {
    flex-shrink: 0;
  }
}

.token-mobile-records {
  display: none;
}
.token-run-id {
  display: block;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--color-text-secondary);
}
@media (max-width: 680px) {
  .token-desktop-records {
    display: none;
  }
  .token-mobile-records {
    display: grid;
    gap: 12px;
    min-width: 0;
  }
  .token-mobile-records article {
    display: grid;
    gap: 8px;
    min-width: 0;
    border: 1px solid var(--gray-150);
    border-radius: 10px;
    padding: 14px;
  }
  .token-mobile-records header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
  }
  .token-mobile-records strong {
    font-weight: 500;
  }
  .token-mobile-records p {
    margin: 0;
    font-size: 13px;
    color: var(--color-text-secondary);
  }
  .token-mobile-records .ant-btn {
    justify-self: start;
    padding-left: 0;
  }
  .token-mobile-records .token-run-id {
    max-width: 100%;
  }
}
</style>
