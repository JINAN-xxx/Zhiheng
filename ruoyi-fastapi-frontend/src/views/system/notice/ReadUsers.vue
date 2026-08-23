<template>
  <ResourceDrawer :open="visible" :title="`「${noticeTitle}」已读用户`" :width="760" @close="close">
    <a-form class="query-form" layout="inline" @submit.prevent>
      <a-form-item><a-input v-model:value="queryParams.searchValue" allow-clear placeholder="登录名称 / 用户名称" @press-enter="handleQuery" @change="handleClear"><template #prefix><SearchOutlined /></template></a-input></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
      <span class="read-stat">共 <strong>{{ total }}</strong> 人已读</span>
    </a-form>
    <ResourceTable :columns="columns" :rows="userList" :loading="loading" :pagination="false" row-key="userId" :scroll="{ x: 680, y: 340 }">
      <template #bodyCell="{ column, record, index }"><template v-if="column.key === 'index'">{{ (queryParams.pageNum - 1) * queryParams.pageSize + index + 1 }}</template><template v-else-if="column.key === 'readTime'">{{ parseTime(record.readTime) }}</template></template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" show-size-changer @change="getList" />
  </ResourceDrawer>
</template>

<script setup name="ReadUsers">
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import { listNoticeReadUsers } from '@/api/system/notice'

const visible = ref(false)
const loading = ref(false)
const noticeTitle = ref('')
const total = ref(0)
const userList = ref([])
const queryParams = reactive({ pageNum: 1, pageSize: 10, noticeId: undefined, searchValue: undefined })
let requestGeneration = 0
const columns = [
  { title: '序号', key: 'index', width: 65, align: 'center' },
  { title: '登录名称', dataIndex: 'userName', key: 'userName', ellipsis: true },
  { title: '用户名称', dataIndex: 'nickName', key: 'nickName', ellipsis: true },
  { title: '所属部门', dataIndex: 'deptName', key: 'deptName', ellipsis: true, mobileHidden: true },
  { title: '手机号码', dataIndex: 'phonenumber', key: 'phonenumber', width: 120, mobileHidden: true },
  { title: '阅读时间', dataIndex: 'readTime', key: 'readTime', width: 170 },
]

function open(row) {
  if (queryParams.noticeId !== row.noticeId) {
    userList.value = []
    total.value = 0
  }
  queryParams.noticeId = row.noticeId
  noticeTitle.value = row.noticeTitle
  queryParams.searchValue = undefined
  queryParams.pageNum = 1
  visible.value = true
  getList()
}
async function getList() {
  const generation = ++requestGeneration
  const noticeId = queryParams.noticeId
  loading.value = true
  try {
    const response = await listNoticeReadUsers({ ...queryParams })
    if (generation !== requestGeneration || !visible.value || noticeId !== queryParams.noticeId) return
    userList.value = response.rows
    total.value = response.total
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}
function handleQuery() { queryParams.pageNum = 1; getList() }
function resetQuery() { queryParams.searchValue = undefined; handleQuery() }
function handleClear(event) { if (!event.target.value) handleQuery() }
function close() { requestGeneration += 1; visible.value = false; loading.value = false; userList.value = []; total.value = 0; queryParams.noticeId = undefined; queryParams.searchValue = undefined }
onBeforeUnmount(() => { requestGeneration += 1 })
defineExpose({ open })
</script>

<style scoped>
.query-form { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.read-stat { margin-left: auto; color: var(--workspace-text-secondary); font-size: 13px; }
.read-stat strong { color: var(--workspace-main); font-size: 15px; }
:deep(.ant-pagination) { justify-content: flex-end; margin-top: 14px; }
</style>
