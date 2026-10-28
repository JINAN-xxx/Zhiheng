<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <section class="usage-panel">
    <WorkspaceState
      v-if="!canQuery"
      kind="forbidden"
      title="无权查看调用记录"
      message="当前账号缺少应用查询权限。"
    />
    <WorkspaceState v-else-if="loading && !rows.length" kind="loading" />
    <WorkspaceState
      v-else-if="error && !rows.length"
      kind="error"
      :message="error"
      @retry="$emit('retry')"
    />
    <WorkspaceState
      v-else-if="!rows.length"
      kind="empty"
      title="暂无调用记录"
      message="公开接口调用完成后会在此展示。"
    />
    <div v-else class="usage-list">
      <div v-if="error" class="stale-notice">刷新失败，当前用量数据可能已过期。</div>
      <div class="usage-mobile-list">
        <article v-for="row in rows" :key="usageRowKey(row)" class="usage-mobile-item">
          <strong>{{ formatTime(row.startedAt) }}</strong>
          <span
            >{{ row.modelCode }} · {{ operationTypeLabel(row.operationType) }} ·
            {{ statusLabel(row.status) }}</span
          >
          <span
            >输入 / 输出 Token：{{ row.inputTokens ?? '待核对' }} /
            {{ row.outputTokens ?? '待核对' }}</span
          >
          <span>检索单元：{{ row.searchUnits ?? '待核对' }}</span>
          <span>
            费用：{{ row.usageStatus === 'unknown' ? '待核对' : formatCost(row.billableCost) }}
            <template v-if="row.usageStatus !== 'unknown' && row.billableCost !== null">{{
              row.currency
            }}</template>
            · {{ usageStatusLabel(row.usageStatus) }}
          </span>
        </article>
      </div>
      <ResourceTable
        class="usage-table"
        :columns="columns"
        :rows="rows"
        :scroll-x="1030"
        :row-key="usageRowKey"
      >
        <template #bodyCell="{ column, record: row }">
          <template v-if="column.key === 'startedAt'">{{ formatTime(row.startedAt) }}</template>
          <template v-else-if="column.key === 'operationType'">{{
            operationTypeLabel(row.operationType)
          }}</template>
          <template v-else-if="column.key === 'tokens'"
            >{{ row.inputTokens ?? '待核对' }} / {{ row.outputTokens ?? '待核对' }}</template
          >
          <template v-else-if="column.key === 'searchUnits'">{{
            row.searchUnits ?? '待核对'
          }}</template>
          <template v-else-if="column.key === 'billableCost'"
            >{{ row.usageStatus === 'unknown' ? '待核对' : formatCost(row.billableCost) }}
            <template v-if="row.usageStatus !== 'unknown' && row.billableCost !== null">{{
              row.currency
            }}</template></template
          >
          <template v-else-if="column.key === 'usageStatus'"
            ><a-tag :color="row.usageStatus === 'known' ? 'success' : 'warning'">{{
              usageStatusLabel(row.usageStatus)
            }}</a-tag></template
          >
          <template v-else-if="column.key === 'status'"
            ><a-tag>{{ statusLabel(row.status) }}</a-tag></template
          >
        </template>
      </ResourceTable>
      <a-pagination
        v-if="total > pageSize"
        :current="pageNum"
        :page-size="pageSize"
        :total="total"
        :page-size-options="['10', '20', '50']"
        show-size-changer
        size="small"
        @change="(page, size) => $emit('page-change', page, size)"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { Pagination as APagination, Tag as ATag } from 'ant-design-vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import type { ApplicationUsage } from '../api/index.ts'
withDefaults(
  defineProps<{
    rows: ApplicationUsage[]
    total: number
    loading?: boolean
    error?: string
    canQuery?: boolean
    pageNum: number
    pageSize: number
  }>(),
  { loading: false, error: '', canQuery: false }
)
defineEmits<{ retry: []; 'page-change': [page: number, size: number] }>()
const columns = [
  { title: '开始时间', dataIndex: 'startedAt', key: 'startedAt', width: 150 },
  { title: '类型', key: 'operationType', width: 100 },
  { title: '模型', dataIndex: 'modelCode', key: 'modelCode', width: 150, ellipsis: true },
  { title: '输入 / 输出 Token', key: 'tokens', width: 160 },
  { title: '检索单元', key: 'searchUnits', width: 110 },
  { title: '结算成本', key: 'billableCost', width: 130 },
  { title: '运行状态', key: 'status', width: 90 },
  { title: '核算状态', key: 'usageStatus', width: 100 }
]
const operationTypeLabel = (value: string) =>
  ({ chat: '对话', embedding: '向量', rerank: '排序' })[value] ?? value
const statusLabel = (value: string) =>
  ({
    completed: '成功',
    failed: '失败',
    cancelled: '已取消',
    interrupted: '已中断',
    running: '运行中',
    pending: '待执行',
    cancel_requested: '正在停止'
  })[value] ?? value
const usageStatusLabel = (value: string) =>
  ({ known: '已核算', unknown: '待核对', pending: '待核算' })[value] ?? value
const usageRowKey = (row: ApplicationUsage) => row.id
const formatTime = (value: string) => new Date(value).toLocaleString('zh-CN')
const formatCost = (value: string | null) => (value === null ? '待核对' : value)
</script>

<style scoped lang="scss">
.usage-list {
  display: grid;
  gap: 12px;
  padding-bottom: 14px;
}

.usage-list > :deep(.ant-pagination) {
  padding: 0 14px;
  justify-content: flex-end;
}

.stale-notice {
  padding: 8px 14px;
  color: var(--color-warning-900);
  background: var(--color-warning-50);
  font-size: 12px;
}

.usage-mobile-list {
  display: none;
}

.usage-mobile-item {
  border-bottom: 1px solid var(--gray-150);
}
</style>

<style scoped lang="scss">
@media (max-width: 760px) {
  .usage-mobile-list {
    display: grid;
    gap: 0;
  }
  .usage-mobile-item {
    display: grid;
    gap: 8px;
    padding: 18px 0;
    color: var(--color-text-secondary);
    font-size: 13px;
    overflow-wrap: anywhere;
  }
  .usage-mobile-item strong {
    color: var(--color-text);
    font-weight: 500;
  }
  .usage-table {
    display: none;
  }
}
</style>
