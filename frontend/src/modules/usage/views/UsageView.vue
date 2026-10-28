<!-- Adapted from original usage ledger, MIT licensed. -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Alert as AAlert, Button as AButton, Select as ASelect, Tag as ATag } from 'ant-design-vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import type { TableColumnsType } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { useWindowSize } from '@vueuse/core'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getUsage } from '../api/index.ts'
import type { UsageSummary } from '../api/index.ts'
const summary = ref<UsageSummary | null>(null)
const { width } = useWindowSize()
const isMobile = computed(() => width.value < 768)
const error = ref('')
const loading = ref(false)
const scope = new RequestScope()
const selectedSource = ref<string>('all')
const selectedStatus = ref<string>('all')
const rows = computed(() =>
  (summary.value?.records ?? []).filter(
    (record) =>
      (selectedSource.value === 'all' || record.source === selectedSource.value) &&
      (selectedStatus.value === 'all' || record.status === selectedStatus.value)
  )
)
const columns: TableColumnsType<UsageSummary['records'][number]> = [
  { title: '开始时间', key: 'created_at', width: 180 },
  { title: '类型', key: 'source', width: 120 },
  { title: '运行', dataIndex: 'run_id', key: 'run_id', width: 180, ellipsis: true },
  { title: '令牌（输入 / 输出）', key: 'tokens', width: 170 },
  { title: '计入用量', key: 'charged', width: 120 },
  { title: '状态', key: 'status', width: 110 }
]
const sourceOptions = [
  { value: 'all', label: '全部类型' },
  { value: 'chat', label: '对话' },
  { value: 'connection_test', label: '连接测试' },
  { value: 'public', label: '开发接入' },
  { value: 'agent_preview', label: '智能体试聊' },
  { value: 'agent_invocation', label: '智能体 API' },
  { value: 'work_turn', label: '工作执行' }
]
const statusOptions = [
  { value: 'all', label: '全部状态' },
  { value: 'known', label: '已核算' },
  { value: 'pending', label: '待核算' },
  { value: 'unknown', label: '待核对' }
]
function setSource(value: unknown): void {
  selectedSource.value = typeof value === 'string' ? value : 'all'
}
function setStatus(value: unknown): void {
  selectedStatus.value = typeof value === 'string' ? value : 'all'
}
const sourceLabel = (source: string): string =>
  ({
    chat: '对话',
    connection_test: '连接测试',
    public: '开发接入',
    agent_preview: '智能体试聊',
    agent_invocation: '智能体 API',
    work_turn: '工作执行'
  })[source] ?? source
const tokens = (value: number | null): string =>
  value === null ? '待核对' : value.toLocaleString()
const recordStatus = (status: string): string =>
  ({ known: '已核算', unknown: '待核对', pending: '待核算' })[status] ?? status
onBeforeUnmount(() => {
  scope.dispose()
})
onMounted(() => {
  void load()
})
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await getUsage(context.signal)
    if (context.isCurrent()) summary.value = result
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
</script>
<template>
  <main class="usage-ledger-page" aria-label="用量记录">
    <WorkspaceToolbar title="用量记录"
      ><template #actions
        ><AButton :loading="loading" aria-label="刷新用量记录" @click="load"
          ><ReloadOutlined />刷新</AButton
        ></template
      ></WorkspaceToolbar
    >
    <section class="usage-content">
      <section v-if="summary" class="usage-quota" aria-label="Token 额度">
        <div>
          <span>已使用</span><strong>{{ tokens(summary.used_tokens) }}</strong
          ><small>Token</small>
        </div>
        <div>
          <span>预留中</span><strong>{{ tokens(summary.reserved_tokens) }}</strong
          ><small>Token</small>
        </div>
        <div>
          <span>配额上限</span><strong>{{ tokens(summary.limit_tokens) }}</strong
          ><small>Token</small>
        </div>
      </section>
      <section class="usage-controls" aria-label="用量筛选">
        <label class="usage-filter"
          ><span>调用类型</span
          ><ASelect
            :value="selectedSource"
            :options="sourceOptions"
            aria-label="调用类型"
            @update:value="setSource" /></label
        ><label class="usage-filter"
          ><span>调用状态</span
          ><ASelect
            :value="selectedStatus"
            :options="statusOptions"
            aria-label="调用状态"
            @update:value="setStatus" /></label
        ><span class="usage-total">共 {{ rows.length }} 条</span>
      </section>
      <AAlert
        v-if="error && summary"
        :message="error"
        type="warning"
        show-icon
        class="space-bottom"
      />
      <WorkspaceState v-if="loading && !summary" kind="loading" />
      <WorkspaceState v-else-if="error && !summary" kind="error" :message="error" @retry="load" />
      <WorkspaceState
        v-else-if="!rows.length"
        title="暂无用量记录"
        message="对话与连接测试调用后会在这里形成台账。"
      />
      <section v-else class="usage-table-wrap">
        <div v-if="isMobile" class="usage-mobile-list">
          <article v-for="row in rows" :key="row.id" class="usage-mobile-item">
            <div class="usage-mobile-item__header">
              <strong>{{
                new Date(row.created_at).toLocaleString('zh-CN', { hour12: false })
              }}</strong
              ><ATag>{{ recordStatus(row.status) }}</ATag>
            </div>
            <div class="usage-mobile-item__model" :title="row.run_id">{{ row.run_id }}</div>
            <div class="usage-mobile-item__meta">
              <ATag>{{ sourceLabel(row.source) }}</ATag
              ><span>令牌 {{ tokens(row.input_tokens) }} / {{ tokens(row.output_tokens) }}</span
              ><span
                >计入用量
                {{ row.status === 'unknown' ? '待核对' : tokens(row.charged_tokens) }}</span
              >
            </div>
          </article>
        </div>
        <ResourceTable v-else class="usage-table" :columns="columns" :rows="rows" :loading="loading"
          ><template #bodyCell="{ column, record }">
            <template v-if="column.key === 'created_at'">{{
              new Date(record.created_at).toLocaleString('zh-CN', { hour12: false })
            }}</template>
            <template v-else-if="column.key === 'source'"
              ><ATag>{{ sourceLabel(record.source) }}</ATag></template
            >
            <template v-else-if="column.key === 'tokens'"
              >{{ tokens(record.input_tokens) }} / {{ tokens(record.output_tokens) }}</template
            >
            <template v-else-if="column.key === 'charged'">{{
              record.status === 'unknown' ? '待核对' : tokens(record.charged_tokens)
            }}</template>
            <template v-else-if="column.key === 'status'"
              ><ATag :color="record.status === 'known' ? 'success' : 'warning'">{{
                recordStatus(record.status)
              }}</ATag></template
            >
          </template></ResourceTable
        >
      </section>
      <p v-if="summary" class="muted small">预留用量会在运行完成后结算；未知用量将显示为待核对。</p>
    </section>
  </main>
</template>
<style scoped lang="scss">
.usage-ledger-page {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
  color: var(--color-text);
  background: var(--workspace-bg-page, #f7f8fc);
  font-family: var(--workspace-font-family);
}
.usage-content {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  flex-direction: column;
  overflow: auto;
  padding: 20px 24px 28px;
}
.usage-controls {
  display: flex;
  flex-wrap: wrap;
  flex: 0 0 auto;
  min-height: 34px;
  min-width: 0;
  align-items: center;
  gap: 10px;
  margin: 0 0 20px;
  padding: 0;
}
.usage-controls :deep(.ant-select) {
  width: 132px;
  min-width: 0;
}
.usage-controls :deep(.ant-select:first-child) {
  width: min(360px, 42%);
}
.usage-total {
  margin-left: auto;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.usage-controls :deep(.ant-select-selector),
.usage-controls :deep(.ant-select-selection-item) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.usage-stale {
  margin: 0 0 10px;
  padding: 9px 12px;
  border-radius: 8px;
  color: var(--color-warning-900);
  background: var(--color-warning-50);
  font-size: 12px;
}
.usage-table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.usage-table-wrap :deep(.ant-table-thead > tr > th) {
  border-bottom-color: var(--gray-150);
  background: var(--gray-10);
  color: var(--color-text-secondary);
}
.usage-table-wrap :deep(.ant-table-tbody > tr > td) {
  border-bottom-color: var(--gray-150);
  background: var(--bg-container);
  color: var(--color-text);
}
.usage-mobile-list {
  display: grid;
  gap: 14px;
}
.usage-mobile-item {
  min-width: 0;
  padding: 17px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.usage-mobile-item__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.usage-mobile-item__header strong {
  font-size: 13px;
}
.usage-mobile-item__model {
  margin-top: 8px;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-family: var(--workspace-font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.usage-mobile-item__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
  margin-top: 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.usage-table-wrap :deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin: 18px 16px;
}
@media (min-width: 992px) {
  .usage-content {
    padding: 0 var(--workspace-space-6) var(--workspace-space-6);
  }
}

@media (max-width: 760px) {
  .usage-controls :deep(.ant-select:first-child) {
    width: 100%;
    flex: 1 1 100%;
  }
  .usage-controls :deep(.ant-select) {
    flex: 1;
  }
  .usage-total {
    flex: 1 1 100%;
    text-align: right;
  }
  .usage-table-wrap {
    border: 0;
    border-radius: 0;
  }
  .usage-table-wrap :deep(.ant-pagination) {
    justify-content: center;
  }
  .usage-table-wrap :deep(.ant-pagination-options) {
    display: none;
  }
}
@media (max-width: 640px) {
  .usage-content {
    padding: 16px;
  }
}
</style>

<style scoped>
.usage-quota {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-bottom: 24px;
}
.usage-quota > div {
  min-width: 0;
  padding: 22px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.usage-quota span {
  display: block;
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.usage-quota strong {
  font-size: 25px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.usage-quota small {
  display: block;
  margin-top: 5px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.usage-filter {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.usage-filter :deep(.ant-select) {
  width: 132px;
}
.usage-table-wrap {
  flex: 0 0 auto;
}
@media (max-width: 760px) {
  .usage-quota {
    gap: 8px;
    margin-bottom: 18px;
  }
  .usage-quota > div {
    padding: 14px 10px;
  }
  .usage-quota strong {
    font-size: 19px;
  }
  .usage-filter {
    flex: 1;
    min-width: 0;
    flex-wrap: wrap;
    gap: 6px;
  }
  .usage-filter :deep(.ant-select) {
    width: 100%;
    flex: 1 1 100%;
  }
}
</style>
