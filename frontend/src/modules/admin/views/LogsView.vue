<script setup lang="ts">
import { singleSelection } from '../formValues.ts'
import { reactive } from 'vue'
import { Tag as ATag, Select as ASelect } from 'ant-design-vue'
import { decodeLog } from '../api/index.ts'
import { useReadCollection } from '../composables/useReadCollection.ts'
import AdminPage from '../components/AdminPage.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { formatAdminTime } from '../presentation.ts'
const props = defineProps<{ kind: 'login' | 'operation' }>()
const filters = reactive({ kind: props.kind, outcome: '' })
const { rows, total, page, search, loading, error, load, find, changePage } = useReadCollection(
  'logs',
  decodeLog,
  filters
)
function filter(): void {
  page.value = 1
  void load()
}
function changeOutcome(value: unknown): void {
  filters.outcome = singleSelection(value)
  filter()
}
const columns = [
  { title: '时间', key: 'time', width: 180 },
  { title: '账号', dataIndex: 'username', key: 'username', width: 140 },
  { title: '操作', dataIndex: 'action', key: 'action', width: 180 },
  { title: '资源', key: 'resource', width: 210 },
  { title: '结果', key: 'outcome', width: 90 },
  { title: '详情', dataIndex: 'message', key: 'message', width: 260 }
]
</script>
<template>
  <AdminPage
    search-placeholder="搜索账号或操作"
    :title="kind === 'login' ? '登录日志' : '操作日志'"
    subtitle="查看账号访问和管理操作的审计记录"
    :loading="loading"
    :error="error"
    :total="total"
    :page="page"
    :search="search"
    @refresh="load"
    @search="find"
    @page="changePage"
    ><template #filters
      ><ASelect
        :value="filters.outcome"
        class="admin-filter"
        aria-label="操作结果"
        :options="[
          { value: '', label: '全部结果' },
          { value: 'success', label: '成功' },
          { value: 'failed', label: '失败' }
        ]"
        @update:value="changeOutcome" /></template
    ><ResourceTable
      :loading-delay="150"
      :columns="columns"
      :rows="rows"
      :loading="loading"
      empty-text="暂无日志"
      ><template #bodyCell="{ column, record }"
        ><span v-if="column.key === 'time'">{{ formatAdminTime(record.created_at) }}</span>
        <div v-else-if="column.key === 'resource'" class="admin-primary">
          <span>{{ record.resource_type || '—' }}</span
          ><small class="admin-code">{{ record.resource_id || '' }}</small>
        </div>
        <ATag
          v-else-if="column.key === 'outcome'"
          :color="record.outcome === 'success' ? 'green' : 'red'"
          >{{ record.outcome === 'success' ? '成功' : '失败' }}</ATag
        ></template
      ></ResourceTable
    ></AdminPage
  >
</template>
