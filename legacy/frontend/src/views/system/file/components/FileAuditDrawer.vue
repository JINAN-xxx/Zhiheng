<template>
  <ResourceDrawer :open="visible" :title="`文件审计 - ${fileName}`" width="80%" @close="closeDrawer">
    <a-form ref="queryRef" :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="操作类型" name="action"><a-select v-model:value="queryParams.action" allow-clear placeholder="全部操作" :options="actionOptions" /></a-form-item>
      <a-form-item label="操作结果" name="result"><a-select v-model:value="queryParams.result" allow-clear placeholder="全部结果" :options="resultOptions" /></a-form-item>
      <a-form-item label="操作用户" name="actorName"><a-input v-model:value="queryParams.actorName" allow-clear placeholder="请输入用户名" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="操作时间"><a-range-picker v-model:value="dateRange" value-format="YYYY-MM-DD HH:mm:ss" show-time :placeholder="['开始时间', '结束时间']" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>
    <ResourceTable :columns="columns" :rows="auditList" :loading="loading" :pagination="false" row-key="auditId" :scroll="{ x: 970 }">
      <template #bodyCell="{ column, record: row }">
        <template v-if="column.key === 'action'">{{ actionLabel(row.action) }}</template>
        <template v-else-if="column.key === 'result'"><a-tag :color="tagColor(resultTagType(row.result))">{{ resultLabel(row.result) }}</a-tag></template>
        <template v-else-if="column.key === 'bytesSent'">{{ row.bytesSent ? formatFileSize(row.bytesSent) : '-' }}</template>
        <template v-else-if="column.key === 'accessTime'">{{ parseTime(row.accessTime) }}</template>
        <template v-else-if="column.key === 'detail'"><a-tooltip title="查看审计详情"><a-button type="link" aria-label="查看审计详情" @click="handleDetail(row)"><EyeOutlined /></a-button></a-tooltip></template>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" show-size-changer @change="getList" />
    <template #footer><a-button @click="closeDrawer">关闭</a-button></template>
  </ResourceDrawer>

  <a-modal v-model:open="detailOpen" title="审计详情" :width="720">
    <a-descriptions :column="2" bordered class="audit-descriptions">
      <a-descriptions-item label="操作类型">{{ actionLabel(detail.action) }}</a-descriptions-item><a-descriptions-item label="操作结果">{{ resultLabel(detail.result) }}</a-descriptions-item>
      <a-descriptions-item label="操作用户">{{ detail.actorName || '-' }}</a-descriptions-item><a-descriptions-item label="客户端地址">{{ detail.ipAddress || '-' }}</a-descriptions-item>
      <a-descriptions-item label="请求ID">{{ detail.requestId || '-' }}</a-descriptions-item><a-descriptions-item label="链路ID">{{ detail.traceId || '-' }}</a-descriptions-item>
      <a-descriptions-item label="操作时间" :span="2">{{ parseTime(detail.accessTime) }}</a-descriptions-item><a-descriptions-item label="用户代理" :span="2">{{ detail.userAgent || '-' }}</a-descriptions-item><a-descriptions-item label="失败原因" :span="2">{{ detail.errorMessage || '-' }}</a-descriptions-item>
    </a-descriptions>
    <ResourceTable v-if="detailEntries.length" class="mt20" :columns="detailColumns" :rows="detailEntries" :pagination="false" :row-key="(_, index) => index" />
    <a-empty v-else description="无操作详情" />
    <template #footer><a-button @click="detailOpen = false">关闭</a-button></template>
  </a-modal>
</template>

<script setup>
import { EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import { listFileAccessLog } from "@/api/system/file";
import {
  actionLabel,
  formatFileSize,
  parseOperationDetail,
  resultLabel,
  resultTagType
} from "./fileFormatters";

const { proxy } = getCurrentInstance();
const visible = ref(false);
const loading = ref(false);
const fileId = ref("");
const fileName = ref("");
const auditList = ref([]);
const total = ref(0);
const dateRange = ref([]);
const queryRef = ref();
const detailOpen = ref(false);
const detail = ref({});
let requestGeneration = 0;
const detailEntries = computed(() =>
  parseOperationDetail(detail.value.operationDetail)
);
const queryParams = reactive({
  pageNum: 1,
  pageSize: 10,
  action: undefined,
  result: undefined,
  actorName: undefined
});
const actionOptions = ['upload', 'download', 'acl_update', 'transfer', 'delete', 'restore', 'purge', 'reconcile', 'retention_extend', 'retention_dispose'].map(value => ({ value, label: actionLabel(value) }));
const resultOptions = ['allowed', 'denied', 'completed', 'failed'].map(value => ({ value, label: resultLabel(value) }));
const columns = [
  { title: '操作', key: 'action', width: 105 }, { title: '结果', key: 'result', width: 100 }, { title: '操作用户', dataIndex: 'actorName', key: 'actorName', width: 120, ellipsis: true },
  { title: '客户端地址', dataIndex: 'ipAddress', key: 'ipAddress', width: 140, ellipsis: true }, { title: '传输字节', key: 'bytesSent', width: 110 },
  { title: '失败原因', dataIndex: 'errorMessage', key: 'errorMessage', width: 160, ellipsis: true }, { title: '访问时间', key: 'accessTime', width: 180 }, { title: '详情', key: 'detail', width: 70 },
];
const detailColumns = [{ title: '详情项', dataIndex: 'label', key: 'label', width: 180 }, { title: '内容', dataIndex: 'value', key: 'value', ellipsis: true }];
const tagColor = type => ({ success: 'green', danger: 'red', warning: 'orange', info: 'default', primary: 'blue' }[type] || type);

function open(row) {
  fileId.value = row.fileId;
  fileName.value = row.originalName;
  Object.assign(queryParams, {
    pageNum: 1,
    action: undefined,
    result: undefined,
    actorName: undefined
  });
  dateRange.value = [];
  auditList.value = [];
  total.value = 0;
  visible.value = true;
  getList();
}

function getList() {
  const generation = ++requestGeneration;
  const requestFileId = fileId.value;
  loading.value = true;
  listFileAccessLog(
    requestFileId,
    proxy.addDateRange({ ...queryParams }, dateRange.value)
  )
    .then(response => {
      if (generation !== requestGeneration || !visible.value || requestFileId !== fileId.value) return;
      auditList.value = response.rows;
      total.value = response.total;
    })
    .finally(() => {
      if (generation === requestGeneration) loading.value = false;
    });
}

function handleQuery() {
  queryParams.pageNum = 1;
  getList();
}

function resetQuery() {
  dateRange.value = [];
  queryRef.value?.resetFields();
  queryParams.pageNum = 1;
  getList();
}

function handleDetail(row) {
  detail.value = row;
  detailOpen.value = true;
}

function closeDrawer() { requestGeneration += 1; visible.value = false; loading.value = false; detailOpen.value = false; auditList.value = []; total.value = 0; }

defineExpose({ open });
onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped>
.query-form { margin-bottom: 16px; row-gap: 8px; }
.audit-descriptions :deep(.ant-descriptions-item-label) {
  white-space: nowrap;
}

.audit-descriptions :deep(.ant-descriptions-item-content) {
  overflow-wrap: anywhere;
  word-break: break-word;
}
</style>
