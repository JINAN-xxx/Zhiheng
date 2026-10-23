<template>
  <section class="usage-panel">
    <WorkspaceState v-if="!canQuery" kind="forbidden" title="无权查看调用记录" message="当前账号缺少应用查询权限。" />
    <WorkspaceState v-else-if="loading && !rows.length" kind="loading" />
    <WorkspaceState v-else-if="error && !rows.length" kind="error" :message="error" @retry="$emit('retry')" />
    <WorkspaceState v-else-if="!rows.length" kind="empty" title="暂无调用记录" message="公开接口调用完成后会在此展示。" />
    <div v-else class="usage-list">
      <div v-if="error" class="stale-notice">刷新失败，当前用量数据可能已过期。</div>
      <div class="usage-mobile-list">
        <article v-for="row in rows" :key="usageRowKey(row)" class="usage-mobile-item">
          <strong>{{ formatTime(row.startedAt) }}</strong>
          <span>{{ row.modelCode }} · {{ operationTypeLabel(row.operationType) }} · {{ statusLabel(row.status) }}</span>
        </article>
      </div>
      <ResourceTable class="usage-table" :columns="columns" :rows="rows" :pagination="false" :scroll="{ x: 860 }" :row-key="usageRowKey">
        <template #bodyCell="{ column, record: row }">
          <template v-if="column.key === 'startedAt'">{{ formatTime(row.startedAt) }}</template>
          <template v-else-if="column.key === 'operationType'">{{ operationTypeLabel(row.operationType) }}</template>
          <template v-else-if="column.key === 'tokens'">{{ row.inputTokens ?? '-' }} / {{ row.outputTokens ?? '-' }}</template>
          <template v-else-if="column.key === 'searchUnits'">{{ row.searchUnits ?? '-' }}</template>
          <template v-else-if="column.key === 'billableCost'">{{ formatCost(row.billableCost) }}<a-tag v-if="row.costEstimated" color="orange">估算</a-tag></template>
          <template v-else-if="column.key === 'status'"><a-tag>{{ statusLabel(row.status) }}</a-tag></template>
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

<script setup>
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'

defineProps({
  rows: { type: Array, default: () => [] },
  total: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  canQuery: { type: Boolean, default: false },
  pageNum: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
})
defineEmits(['retry', 'page-change'])

const columns = [
  { title: '开始时间', dataIndex: 'startedAt', key: 'startedAt', width: 150 },
  { title: '类型', dataIndex: 'operationType', key: 'operationType', width: 100 },
  { title: '模型', dataIndex: 'modelCode', key: 'modelCode', width: 150, ellipsis: true },
  { title: '令牌', key: 'tokens', width: 130 },
  { title: '检索单元', key: 'searchUnits', width: 110 },
  { title: '结算成本', key: 'billableCost', width: 130 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 90 },
]
const operationTypeLabel = (value) => ({ chat: '对话', embedding: '向量', rerank: '排序' }[value] || value || '-')
const statusLabel = (value) => ({ completed: '成功', failed: '失败', cancelled: '已取消', abandoned: '已放弃', started: '处理中' }[value] || value || '-')
const usageRowKey = (row) => row.usageId || row.requestId || row.id || `${row.startedAt}-${row.modelCode}`
const formatTime = (value) => value ? new Date(value).toLocaleString('zh-CN') : '-'
const formatCost = (value) => value == null ? '-' : Number(value).toFixed(6)
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
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
</style>
