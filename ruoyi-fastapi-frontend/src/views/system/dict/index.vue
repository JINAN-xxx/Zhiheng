<template>
  <WorkspacePage title="字典管理" subtitle="维护系统字典类型与缓存">
    <a-form v-show="showSearch" class="query-form" layout="inline" @submit.prevent>
      <a-form-item label="字典名称"><a-input v-model:value="queryParams.dictName" allow-clear placeholder="请输入字典名称" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="字典类型"><a-input v-model:value="queryParams.dictType" allow-clear placeholder="请输入字典类型" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="状态"><a-select v-model:value="queryParams.status" allow-clear placeholder="字典状态" :options="normalOptions" /></a-form-item>
      <a-form-item label="创建时间"><a-range-picker v-model:value="dateRange" value-format="YYYY-MM-DD" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>

    <div class="table-toolbar">
      <a-space wrap>
        <a-button type="primary" @click="handleAdd" v-hasPermi="['system:dict:add']"><PlusOutlined />新增</a-button>
        <a-button :disabled="single" @click="handleUpdate()" v-hasPermi="['system:dict:edit']"><EditOutlined />修改</a-button>
        <a-button danger :disabled="multiple" @click="handleDelete()" v-hasPermi="['system:dict:remove']"><DeleteOutlined />删除</a-button>
        <a-button @click="handleExport" v-hasPermi="['system:dict:export']"><DownloadOutlined />导出</a-button>
        <a-button danger @click="handleRefreshCache" v-hasPermi="['system:dict:remove']"><SyncOutlined />刷新缓存</a-button>
      </a-space>
      <a-space><a-tooltip title="显示/隐藏筛选"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip><a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip></a-space>
    </div>

    <ResourceTable :columns="columns" :rows="typeList" :loading="loading" :pagination="false" row-key="dictId" :scroll="{ x: 1040 }">
      <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'selection'"><a-checkbox :checked="ids.includes(record.dictId)" @change="(event) => toggleRow(record, event.target.checked)" /></template>
        <template v-else-if="column.key === 'dictType'"><a-button type="link" class="cell-link" @click="handleViewData(record)">{{ record.dictType }}</a-button></template>
        <template v-else-if="column.key === 'status'"><dict-tag :options="sys_normal_disable" :value="record.status" /></template>
        <template v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime) }}</template>
        <template v-else-if="column.key === 'actions'">
          <a-space class="desktop-row-actions" size="small"><a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:dict:edit']"><EditOutlined />修改</a-button><a-button type="link" @click="handleDataList(record)" v-hasPermi="['system:dict:edit']"><UnorderedListOutlined />列表</a-button><a-button type="link" danger @click="handleDelete(record)" v-hasPermi="['system:dict:remove']"><DeleteOutlined />删除</a-button></a-space>
          <WorkspaceMoreMenu v-if="mobileRowActions.length" class="mobile-row-actions" :items="mobileRowActions" @command="(command) => handleRowCommand(command, record)" />
        </template>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" :page-size-options="['10', '20', '50']" show-size-changer @change="getList" />

    <ResourceDrawer :open="open" :title="title" :width="520" destroy-on-close @close="cancel">
      <a-form ref="dictRef" :model="form" :rules="rules" layout="vertical">
        <a-form-item label="字典名称" name="dictName"><a-input v-model:value="form.dictName" placeholder="请输入字典名称" /></a-form-item>
        <a-form-item name="dictType"><template #label><a-tooltip title="数据存储中的 Key 值，如：sys_user_sex"><QuestionCircleOutlined /></a-tooltip><span class="label-text">字典类型</span></template><a-input v-model:value="form.dictType" placeholder="请输入字典类型" /></a-form-item>
        <a-form-item label="状态" name="status"><a-radio-group v-model:value="form.status" :options="normalOptions" /></a-form-item>
        <a-form-item label="备注" name="remark"><a-textarea v-model:value="form.remark" placeholder="请输入内容" :rows="4" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="cancel">取消</a-button><a-button type="primary" @click="submitForm">确定</a-button></template>
    </ResourceDrawer>
    <DictDataDrawer v-model:visible="drawerVisible" :row="drawerRow" />
  </WorkspacePage>
</template>

<script setup name="Dict">
import { DeleteOutlined, DownloadOutlined, EditOutlined, FilterOutlined, PlusOutlined, QuestionCircleOutlined, ReloadOutlined, SyncOutlined, UnorderedListOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
import DictDataDrawer from './detail.vue'
import useDictStore from '@/store/modules/dict'
import { checkPermi } from '@/utils/permission'
import { useWindowSize } from '@vueuse/core'
import { listType, getType, delType, addType, updateType, refreshCache } from '@/api/system/dict/type'

const { proxy } = getCurrentInstance()
const { sys_normal_disable } = proxy.useDict('sys_normal_disable')
const typeList = ref([])
const open = ref(false)
const loading = ref(true)
const showSearch = ref(true)
const ids = ref([])
const total = ref(0)
const title = ref('')
const dateRange = ref([])
const drawerVisible = ref(false)
const drawerRow = ref({})
const dictRef = ref()
const queryParams = reactive({ pageNum: 1, pageSize: 10, dictName: undefined, dictType: undefined, status: undefined })
const form = reactive({})
const rules = { dictName: [{ required: true, message: '字典名称不能为空', trigger: 'blur' }], dictType: [{ required: true, message: '字典类型不能为空', trigger: 'blur' }] }
const normalOptions = computed(() => sys_normal_disable.value.map((item) => ({ label: item.label, value: item.value })))
const { width: viewportWidth } = useWindowSize()
const isMobile = computed(() => viewportWidth.value < 768)
const single = computed(() => ids.value.length !== 1)
const multiple = computed(() => ids.value.length === 0)
const allSelected = computed(() => typeList.value.length > 0 && typeList.value.every((item) => ids.value.includes(item.dictId)))
const someSelected = computed(() => ids.value.length > 0 && !allSelected.value)
const columns = computed(() => [
  { title: '', key: 'selection', width: 48 }, { title: '字典编号', dataIndex: 'dictId', key: 'dictId', width: 100 },
  { title: '字典名称', dataIndex: 'dictName', key: 'dictName', ellipsis: true }, { title: '字典类型', dataIndex: 'dictType', key: 'dictType', ellipsis: true },
  { title: '状态', key: 'status', width: 90 }, { title: '备注', dataIndex: 'remark', key: 'remark', ellipsis: true, mobileHidden: true },
  { title: '创建时间', key: 'createTime', width: 180, mobileHidden: true }, { title: '操作', key: 'actions', width: isMobile.value ? 56 : 230, fixed: 'right' },
])
const mobileRowActions = computed(() => [
  checkPermi(['system:dict:edit']) && { command: 'edit', label: '修改', icon: EditOutlined },
  checkPermi(['system:dict:edit']) && { command: 'list', label: '列表', icon: UnorderedListOutlined },
  checkPermi(['system:dict:remove']) && { command: 'delete', label: '删除', icon: DeleteOutlined, danger: true, divided: true },
].filter(Boolean))

async function getList() { loading.value = true; try { const response = await listType(proxy.addDateRange({ ...queryParams }, dateRange.value)); typeList.value = response.rows; total.value = response.total; ids.value = [] } finally { loading.value = false } }
function reset() { Object.assign(form, { dictId: undefined, dictName: undefined, dictType: undefined, status: '0', remark: undefined }); nextTick(() => dictRef.value?.clearValidate()) }
function cancel() { open.value = false; reset() }
function handleQuery() { queryParams.pageNum = 1; getList() }
function resetQuery() { dateRange.value = []; Object.assign(queryParams, { dictName: undefined, dictType: undefined, status: undefined }); handleQuery() }
function handleAdd() { reset(); open.value = true; title.value = '添加字典类型' }
function toggleRow(row, checked) { ids.value = checked ? [...new Set([...ids.value, row.dictId])] : ids.value.filter((id) => id !== row.dictId) }
function toggleAll(event) { ids.value = event.target.checked ? typeList.value.map((item) => item.dictId) : [] }
function handleViewData(row) { drawerRow.value = row; drawerVisible.value = true }
function handleDataList(row) { proxy.$tab.openPage('字典数据', `/system/dict-data/index/${row.dictId}`) }
function handleRowCommand(command, row) {
  if (command === 'edit') handleUpdate(row)
  else if (command === 'list') handleDataList(row)
  else if (command === 'delete') handleDelete(row)
}
async function handleUpdate(row = {}) { reset(); const dictId = row.dictId || ids.value[0]; const response = await getType(dictId); Object.assign(form, response.data); open.value = true; title.value = '修改字典类型' }
async function submitForm() { if (!await dictRef.value.validateFields().then(() => true).catch(() => false)) return; if (form.dictId != null) await updateType(form); else await addType(form); proxy.$modal.msgSuccess(form.dictId != null ? '修改成功' : '新增成功'); open.value = false; getList() }
function handleDelete(row = {}) { const dictIds = row.dictId || ids.value; proxy.$modal.confirm(`是否确认删除字典编号为"${dictIds}"的数据项？`).then(() => delType(dictIds)).then(() => { getList(); proxy.$modal.msgSuccess('删除成功') }).catch(() => {}) }
function handleExport() { proxy.download('system/dict/type/export', { ...queryParams }, `dict_${Date.now()}.xlsx`) }
function handleRefreshCache() { refreshCache().then(() => { proxy.$modal.msgSuccess('刷新成功'); useDictStore().cleanDict() }) }
getList()
</script>

<style scoped>
.query-form { padding: 16px; margin-bottom: 12px; border: 1px solid var(--workspace-border); background: var(--workspace-surface); }
.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 210px; }
.table-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.cell-link { padding: 0; }
.mobile-row-actions { display: none; }
.label-text { margin-left: 6px; }
:deep(.ant-pagination) { justify-content: flex-end; margin-top: 14px; }
@media (max-width: 767px) { .query-form { display: grid; } .query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 100%; } .desktop-row-actions { display: none; } .mobile-row-actions { display: inline-flex; } }
</style>
