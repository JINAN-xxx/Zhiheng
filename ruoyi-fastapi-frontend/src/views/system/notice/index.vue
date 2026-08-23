<template>
  <WorkspacePage title="通知公告" subtitle="维护公告内容与阅读记录">
    <a-form v-show="showSearch" class="query-form" layout="inline" @submit.prevent>
      <a-form-item label="公告标题"><a-input v-model:value="queryParams.noticeTitle" allow-clear placeholder="请输入公告标题" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="操作人员"><a-input v-model:value="queryParams.createBy" allow-clear placeholder="请输入操作人员" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="类型"><a-select v-model:value="queryParams.noticeType" allow-clear placeholder="公告类型" :options="noticeTypeOptions" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>
    <div class="table-toolbar"><a-space>
      <a-button type="primary" @click="handleAdd" v-hasPermi="['system:notice:add']"><PlusOutlined />新增</a-button>
      <a-button :disabled="single" @click="handleUpdate()" v-hasPermi="['system:notice:edit']"><EditOutlined />修改</a-button>
      <a-button danger :disabled="multiple" @click="handleDelete()" v-hasPermi="['system:notice:remove']"><DeleteOutlined />删除</a-button>
    </a-space><a-space><a-tooltip title="显示/隐藏筛选"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip><a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip></a-space></div>
    <ResourceTable :columns="columns" :rows="noticeList" :loading="loading" :pagination="false" row-key="noticeId" :scroll="{ x: 960 }">
      <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'selection'"><a-checkbox :checked="ids.includes(record.noticeId)" @change="(event) => toggleRow(record, event.target.checked)" /></template>
        <template v-else-if="column.key === 'noticeTitle'"><a-button type="link" class="cell-link" @click="handleViewData(record)">{{ record.noticeTitle }}</a-button></template>
        <template v-else-if="column.key === 'noticeType'"><dict-tag :options="sys_notice_type" :value="record.noticeType" /></template>
        <template v-else-if="column.key === 'status'"><dict-tag :options="sys_notice_status" :value="record.status" /></template>
        <template v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime, '{y}-{m}-{d}') }}</template>
        <template v-else-if="column.key === 'actions'"><a-space size="small"><a-button type="link" @click="handleReadUsers(record)" v-hasPermi="['system:notice:list']"><TeamOutlined />阅读用户</a-button><a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:notice:edit']"><EditOutlined />修改</a-button><a-button type="link" danger @click="handleDelete(record)" v-hasPermi="['system:notice:remove']"><DeleteOutlined />删除</a-button></a-space></template>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" :page-size-options="['10', '20', '50']" show-size-changer @change="getList" />
    <ResourceDrawer :open="open" :title="title" :width="780" destroy-on-close @close="cancel">
      <a-form ref="noticeRef" :model="form" :rules="rules" layout="vertical">
        <a-row :gutter="16"><a-col :xs="24" :md="12"><a-form-item label="公告标题" name="noticeTitle"><a-input v-model:value="form.noticeTitle" placeholder="请输入公告标题" /></a-form-item></a-col><a-col :xs="24" :md="12"><a-form-item label="公告类型" name="noticeType"><a-select v-model:value="form.noticeType" placeholder="请选择" :options="noticeTypeOptions" /></a-form-item></a-col></a-row>
        <a-form-item label="状态" name="status"><a-radio-group v-model:value="form.status" :options="noticeStatusOptions" /></a-form-item>
        <a-form-item label="内容" name="noticeContent"><editor v-model="form.noticeContent" :min-height="192" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="cancel">取消</a-button><a-button type="primary" @click="submitForm">确定</a-button></template>
    </ResourceDrawer>
    <NoticeDetailView ref="noticeViewRef" />
    <ReadUsersDialog ref="readUsersRef" />
  </WorkspacePage>
</template>

<script setup name="Notice">
import { DeleteOutlined, EditOutlined, FilterOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, TeamOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import NoticeDetailView from '@/layout/components/HeaderNotice/DetailView.vue'
import ReadUsersDialog from './ReadUsers.vue'
import { listNotice, getNotice, delNotice, addNotice, updateNotice } from '@/api/system/notice'

const { proxy } = getCurrentInstance()
const { sys_notice_status, sys_notice_type } = proxy.useDict('sys_notice_status', 'sys_notice_type')
const noticeList = ref([]), open = ref(false), loading = ref(true), showSearch = ref(true), ids = ref([]), total = ref(0), title = ref('')
const noticeRef = ref(), noticeViewRef = ref(), readUsersRef = ref()
const queryParams = reactive({ pageNum: 1, pageSize: 10, noticeTitle: undefined, createBy: undefined, noticeType: undefined, status: undefined })
const form = reactive({})
const rules = { noticeTitle: [{ required: true, message: '公告标题不能为空', trigger: 'blur' }], noticeType: [{ required: true, message: '公告类型不能为空', trigger: 'change' }] }
const noticeTypeOptions = computed(() => sys_notice_type.value.map((item) => ({ label: item.label, value: item.value })))
const noticeStatusOptions = computed(() => sys_notice_status.value.map((item) => ({ label: item.label, value: item.value })))
const single = computed(() => ids.value.length !== 1), multiple = computed(() => ids.value.length === 0)
const allSelected = computed(() => noticeList.value.length > 0 && noticeList.value.every((item) => ids.value.includes(item.noticeId)))
const someSelected = computed(() => ids.value.length > 0 && !allSelected.value)
const columns = [
  { title: '', key: 'selection', width: 48 }, { title: '序号', dataIndex: 'noticeId', key: 'noticeId', width: 90 },
  { title: '公告标题', dataIndex: 'noticeTitle', key: 'noticeTitle', ellipsis: true }, { title: '公告类型', key: 'noticeType', width: 100 },
  { title: '状态', key: 'status', width: 90 }, { title: '创建者', dataIndex: 'createBy', key: 'createBy', width: 110, mobileHidden: true },
  { title: '创建时间', key: 'createTime', width: 120, mobileHidden: true }, { title: '操作', key: 'actions', width: 260, fixed: 'right' },
]

async function getList() { loading.value = true; try { const response = await listNotice(queryParams); noticeList.value = response.rows; total.value = response.total; ids.value = [] } finally { loading.value = false } }
function reset() { Object.keys(form).forEach((key) => delete form[key]); Object.assign(form, { noticeId: undefined, noticeTitle: undefined, noticeType: undefined, noticeContent: undefined, status: '0' }); nextTick(() => noticeRef.value?.clearValidate()) }
function cancel() { open.value = false; reset() }
function handleQuery() { queryParams.pageNum = 1; getList() }
function resetQuery() { Object.assign(queryParams, { noticeTitle: undefined, createBy: undefined, noticeType: undefined, status: undefined }); handleQuery() }
function toggleRow(row, checked) { ids.value = checked ? [...new Set([...ids.value, row.noticeId])] : ids.value.filter((id) => id !== row.noticeId) }
function toggleAll(event) { ids.value = event.target.checked ? noticeList.value.map((item) => item.noticeId) : [] }
function handleAdd() { reset(); open.value = true; title.value = '添加公告' }
async function handleUpdate(row = {}) { reset(); const response = await getNotice(row.noticeId || ids.value[0]); Object.assign(form, response.data); open.value = true; title.value = '修改公告' }
async function submitForm() { if (!await noticeRef.value.validateFields().then(() => true).catch(() => false)) return; const editing = form.noticeId != null; if (editing) await updateNotice(form); else await addNotice(form); proxy.$modal.msgSuccess(editing ? '修改成功' : '新增成功'); open.value = false; getList() }
function handleViewData(row) { noticeViewRef.value?.open(row) }
function handleReadUsers(row) { readUsersRef.value?.open(row) }
function handleDelete(row = {}) { const noticeIds = row.noticeId || ids.value; proxy.$modal.confirm(`是否确认删除公告编号为"${noticeIds}"的数据项？`).then(() => delNotice(noticeIds)).then(() => { getList(); proxy.$modal.msgSuccess('删除成功') }).catch(() => {}) }
getList()
</script>

<style scoped>
.query-form { padding:16px; margin-bottom:12px; border:1px solid var(--workspace-border); background:var(--workspace-surface); }
.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width:210px; }
.table-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
.cell-link { max-width:100%; padding:0; overflow:hidden; text-overflow:ellipsis; }
:deep(.ant-pagination) { justify-content:flex-end; margin-top:14px; }
@media (max-width:767px) { .query-form { display:grid; } .query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select) { width:100%; } }
</style>
