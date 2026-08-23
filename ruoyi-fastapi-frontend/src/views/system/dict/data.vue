<template>
  <WorkspacePage title="字典数据" :subtitle="queryParams.dictType || '维护字典条目'">
    <a-form v-show="showSearch" class="query-form" layout="inline" @submit.prevent>
      <a-form-item label="字典名称"><a-select v-model:value="queryParams.dictType" :options="typeSelectOptions" @change="handleQuery" /></a-form-item>
      <a-form-item label="字典标签"><a-input v-model:value="queryParams.dictLabel" allow-clear placeholder="请输入字典标签" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="状态"><a-select v-model:value="queryParams.status" allow-clear placeholder="数据状态" :options="normalOptions" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>
    <div class="table-toolbar"><a-space wrap>
      <a-button type="primary" @click="handleAdd" v-hasPermi="['system:dict:add']"><PlusOutlined />新增</a-button>
      <a-button :disabled="single" @click="handleUpdate()" v-hasPermi="['system:dict:edit']"><EditOutlined />修改</a-button>
      <a-button danger :disabled="multiple" @click="handleDelete()" v-hasPermi="['system:dict:remove']"><DeleteOutlined />删除</a-button>
      <a-button @click="handleExport" v-hasPermi="['system:dict:export']"><DownloadOutlined />导出</a-button>
      <a-button @click="handleClose"><CloseOutlined />关闭</a-button>
    </a-space><a-space><a-tooltip title="显示/隐藏筛选"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip><a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip></a-space></div>
    <ResourceTable :columns="columns" :rows="dataList" :loading="loading" :pagination="false" row-key="dictCode" :scroll="{ x: 980 }">
      <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'selection'"><a-checkbox :checked="ids.includes(record.dictCode)" @change="(event) => toggleRow(record, event.target.checked)" /></template>
        <template v-else-if="column.key === 'dictLabel'"><span v-if="(!record.listClass || record.listClass === 'default') && !record.cssClass">{{ record.dictLabel }}</span><a-tag v-else :color="tagColor(record.listClass)" :class="record.cssClass">{{ record.dictLabel }}</a-tag></template>
        <template v-else-if="column.key === 'status'"><dict-tag :options="sys_normal_disable" :value="record.status" /></template>
        <template v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime) }}</template>
        <template v-else-if="column.key === 'actions'"><a-space><a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:dict:edit']"><EditOutlined />修改</a-button><a-button type="link" danger @click="handleDelete(record)" v-hasPermi="['system:dict:remove']"><DeleteOutlined />删除</a-button></a-space></template>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" :page-size-options="['10', '20', '50']" show-size-changer @change="getList" />
    <ResourceDrawer :open="open" :title="title" :width="540" destroy-on-close @close="cancel">
      <a-form ref="dataRef" :model="form" :rules="rules" layout="vertical">
        <a-form-item label="字典类型"><a-input v-model:value="form.dictType" disabled /></a-form-item>
        <a-form-item label="数据标签" name="dictLabel"><a-input v-model:value="form.dictLabel" placeholder="请输入数据标签" /></a-form-item>
        <a-form-item label="数据键值" name="dictValue"><a-input v-model:value="form.dictValue" placeholder="请输入数据键值" /></a-form-item>
        <a-form-item label="样式属性" name="cssClass"><a-input v-model:value="form.cssClass" placeholder="请输入样式属性" /></a-form-item>
        <a-form-item label="显示排序" name="dictSort"><a-input-number v-model:value="form.dictSort" :min="0" style="width:100%" /></a-form-item>
        <a-form-item label="回显样式" name="listClass"><a-select v-model:value="form.listClass" :options="listClassOptions" /></a-form-item>
        <a-form-item label="状态" name="status"><a-radio-group v-model:value="form.status" :options="normalOptions" /></a-form-item>
        <a-form-item label="备注" name="remark"><a-textarea v-model:value="form.remark" placeholder="请输入内容" :rows="4" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="cancel">取消</a-button><a-button type="primary" @click="submitForm">确定</a-button></template>
    </ResourceDrawer>
  </WorkspacePage>
</template>

<script setup name="Data">
import { CloseOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FilterOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import useDictStore from '@/store/modules/dict'
import { optionselect as getDictOptionselect, getType } from '@/api/system/dict/type'
import { listData, getData, delData, addData, updateData } from '@/api/system/dict/data'

const { proxy } = getCurrentInstance()
const { sys_normal_disable } = proxy.useDict('sys_normal_disable')
const route = useRoute()
const dataList = ref([]), open = ref(false), loading = ref(true), showSearch = ref(true), ids = ref([]), total = ref(0)
const title = ref(''), defaultDictType = ref(''), typeOptions = ref([]), dataRef = ref()
const queryParams = reactive({ pageNum: 1, pageSize: 10, dictType: undefined, dictLabel: undefined, status: undefined })
const form = reactive({})
const rules = { dictLabel: [{ required: true, message: '数据标签不能为空', trigger: 'blur' }], dictValue: [{ required: true, message: '数据键值不能为空', trigger: 'blur' }], dictSort: [{ required: true, message: '数据顺序不能为空', trigger: 'blur' }] }
const listClassOptions = [
  { value: 'default', label: '默认(default)' }, { value: 'primary', label: '主要(primary)' }, { value: 'success', label: '成功(success)' },
  { value: 'info', label: '信息(info)' }, { value: 'warning', label: '警告(warning)' }, { value: 'danger', label: '危险(danger)' },
]
const normalOptions = computed(() => sys_normal_disable.value.map((item) => ({ label: item.label, value: item.value })))
const typeSelectOptions = computed(() => typeOptions.value.map((item) => ({ label: item.dictName, value: item.dictType })))
const single = computed(() => ids.value.length !== 1), multiple = computed(() => ids.value.length === 0)
const allSelected = computed(() => dataList.value.length > 0 && dataList.value.every((item) => ids.value.includes(item.dictCode)))
const someSelected = computed(() => ids.value.length > 0 && !allSelected.value)
const columns = [
  { title: '', key: 'selection', width: 48 }, { title: '字典编码', dataIndex: 'dictCode', key: 'dictCode', width: 100 }, { title: '字典标签', key: 'dictLabel' },
  { title: '字典键值', dataIndex: 'dictValue', key: 'dictValue' }, { title: '字典排序', dataIndex: 'dictSort', key: 'dictSort', width: 100 },
  { title: '状态', key: 'status', width: 90 }, { title: '备注', dataIndex: 'remark', key: 'remark', ellipsis: true, mobileHidden: true },
  { title: '创建时间', key: 'createTime', width: 180, mobileHidden: true }, { title: '操作', key: 'actions', width: 150, fixed: 'right' },
]
const tagColor = (type) => ({ primary: 'blue', success: 'green', info: 'default', warning: 'orange', danger: 'red' }[type] || 'default')

async function getTypes(dictId) { const response = await getType(dictId); queryParams.dictType = response.data.dictType; defaultDictType.value = response.data.dictType; getList() }
async function getTypeList() { const response = await getDictOptionselect(); typeOptions.value = response.data }
async function getList() { loading.value = true; try { const response = await listData(queryParams); dataList.value = response.rows; total.value = response.total; ids.value = [] } finally { loading.value = false } }
function reset() { Object.keys(form).forEach((key) => delete form[key]); Object.assign(form, { dictCode: undefined, dictLabel: undefined, dictValue: undefined, cssClass: undefined, listClass: 'default', dictSort: 0, status: '0', remark: undefined }); nextTick(() => dataRef.value?.clearValidate()) }
function cancel() { open.value = false; reset() }
function handleQuery() { queryParams.pageNum = 1; getList() }
function handleClose() { proxy.$tab.closeOpenPage({ path: '/system/dict' }) }
function resetQuery() { Object.assign(queryParams, { dictType: defaultDictType.value, dictLabel: undefined, status: undefined }); handleQuery() }
function handleAdd() { reset(); form.dictType = queryParams.dictType; open.value = true; title.value = '添加字典数据' }
function toggleRow(row, checked) { ids.value = checked ? [...new Set([...ids.value, row.dictCode])] : ids.value.filter((id) => id !== row.dictCode) }
function toggleAll(event) { ids.value = event.target.checked ? dataList.value.map((item) => item.dictCode) : [] }
async function handleUpdate(row = {}) { reset(); const response = await getData(row.dictCode || ids.value[0]); Object.assign(form, response.data); open.value = true; title.value = '修改字典数据' }
async function submitForm() { if (!await dataRef.value.validateFields().then(() => true).catch(() => false)) return; const editing = form.dictCode != null; if (editing) await updateData(form); else await addData(form); useDictStore().removeDict(queryParams.dictType); proxy.$modal.msgSuccess(editing ? '修改成功' : '新增成功'); open.value = false; getList() }
function handleDelete(row = {}) { const dictCodes = row.dictCode || ids.value; proxy.$modal.confirm(`是否确认删除字典编码为"${dictCodes}"的数据项？`).then(() => delData(dictCodes)).then(() => { getList(); proxy.$modal.msgSuccess('删除成功'); useDictStore().removeDict(queryParams.dictType) }).catch(() => {}) }
function handleExport() { proxy.download('system/dict/data/export', { ...queryParams }, `dict_data_${Date.now()}.xlsx`) }
getTypes(route.params && route.params.dictId)
getTypeList()
</script>

<style scoped>
.query-form { padding: 16px; margin-bottom: 12px; border: 1px solid var(--workspace-border); background: var(--workspace-surface); }
.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 210px; }
.table-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
:deep(.ant-pagination) { justify-content:flex-end; margin-top:14px; }
@media (max-width:767px) { .query-form { display:grid; } .query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select) { width:100%; } }
</style>
