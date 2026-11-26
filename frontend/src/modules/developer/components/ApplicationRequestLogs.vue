<script setup lang="ts">
import { onActivated, onBeforeUnmount, ref, watch } from 'vue'
import { Pagination as APagination } from 'ant-design-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import { listPublicRequestLogs } from '../api/index.ts'

const props = defineProps<{ applicationId: string; canQuery: boolean }>()
const scope = new RequestScope()
let loadedAt = 0
const rows = ref<Awaited<ReturnType<typeof listPublicRequestLogs>>['rows']>([])
const total = ref(0),
  page = ref(1),
  loading = ref(false),
  error = ref('')
const columns = [
  { title: '请求时间', key: 'createdAt', width: 180 },
  { title: '接口', key: 'route', width: 280 },
  { title: '状态码', dataIndex: 'status', key: 'status', width: 90 },
  { title: '失败原因', dataIndex: 'errorCode', key: 'errorCode', width: 165 },
  { title: '请求编号', dataIndex: 'id', key: 'id', width: 290 }
]
async function load(target = page.value): Promise<void> {
  if (!props.canQuery) return
  const ticket = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await listPublicRequestLogs(props.applicationId, target, ticket.signal)
    if (!ticket.isCurrent()) return
    rows.value = result.rows
    total.value = result.total
    page.value = target
    loadedAt = Date.now()
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) loading.value = false
  }
}
watch(
  () => [props.applicationId, props.canQuery],
  () => {
    scope.replace()
    loadedAt = 0
    rows.value = []
    total.value = 0
    page.value = 1
    loading.value = false
    void load(1)
  },
  { immediate: true }
)
onActivated(() => {
  if (!loading.value && Date.now() - loadedAt >= 30_000) void load()
})
onBeforeUnmount(() => scope.dispose())
</script>

<template>
  <section aria-label="请求诊断">
    <WorkspaceState v-if="!canQuery" kind="forbidden" title="无权查看请求记录" />
    <template v-else>
      <div class="request-toolbar">
        <RefreshButton label="刷新请求记录" :loading="loading" @click="load()" />
      </div>
      <WorkspaceState v-if="error" kind="error" :message="error" @retry="load()" />
      <WorkspaceState v-else-if="loading && !rows.length" kind="loading" />
      <WorkspaceState v-else-if="!rows.length" kind="empty" title="暂无请求诊断" />
      <ResourceTable v-else :rows="rows" :columns="columns" row-key="id" :scroll-x="1005">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'createdAt'">{{
            new Date(record.createdAt).toLocaleString('zh-CN')
          }}</template>
          <code v-else-if="column.key === 'route'">{{ record.method }} {{ record.route }}</code>
          <code v-else-if="column.key === 'errorCode'">{{ record.errorCode ?? '—' }}</code>
        </template>
      </ResourceTable>
      <a-pagination
        v-if="total > 20"
        class="request-toolbar"
        :current="page"
        :total="total"
        :page-size="20"
        :show-size-changer="false"
        size="small"
        @change="load"
      />
    </template>
  </section>
</template>

<style scoped>
.request-toolbar {
  display: flex;
  justify-content: flex-end;
  margin: 8px 14px;
}
</style>
