<template>
  <WorkspacePage title="参数设置" subtitle="系统运行参数与缓存">
    <template #actions><a-space wrap>
      <a-button type="primary" @click="handleAdd" v-hasPermi="['system:config:add']"><PlusOutlined />新增</a-button>
      <a-button :disabled="single" @click="handleUpdate" v-hasPermi="['system:config:edit']"><EditOutlined />修改</a-button>
      <a-button danger :disabled="multiple" @click="handleDelete" v-hasPermi="['system:config:remove']"><DeleteOutlined />删除</a-button>
      <a-button @click="handleExport" v-hasPermi="['system:config:export']"><DownloadOutlined />导出</a-button>
      <a-button danger @click="handleRefreshCache" v-hasPermi="['system:config:remove']"><SyncOutlined />刷新缓存</a-button>
      <a-tooltip title="显示/隐藏搜索"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
      <a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
    </a-space></template>

    <a-form v-show="showSearch" ref="queryRef" :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="参数名称" name="configName"><a-input v-model:value="queryParams.configName" allow-clear placeholder="请输入参数名称" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="参数键名" name="configKey"><a-input v-model:value="queryParams.configKey" allow-clear placeholder="请输入参数键名" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="系统内置" name="configType"><a-select v-model:value="queryParams.configType" allow-clear placeholder="系统内置" :options="yesNoOptions" /></a-form-item>
      <a-form-item label="创建时间"><a-range-picker v-model:value="dateRange" value-format="YYYY-MM-DD" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>

    <ResourceTable :columns="columns" :rows="configList" :loading="loading" :pagination="false" row-key="configId" :scroll="{ x: 1100 }">
      <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="partiallySelected" @change="toggleAll" /></template>
      <template #bodyCell="{ column, record }">
        <a-checkbox v-if="column.key === 'selection'" :checked="ids.includes(record.configId)" @change="(event) => toggleRow(record, event.target.checked)" />
        <dict-tag v-else-if="column.key === 'configType'" :options="sys_yes_no" :value="record.configType" />
        <span v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime) }}</span>
        <template v-else-if="column.key === 'actions'">
          <a-space class="desktop-row-actions">
            <a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:config:edit']"><EditOutlined />修改</a-button>
            <a-button type="link" danger @click="handleDelete(record)" v-hasPermi="['system:config:remove']"><DeleteOutlined />删除</a-button>
          </a-space>
          <div v-if="configRowActions.length" class="mobile-row-actions"><WorkspaceMoreMenu :items="configRowActions" @command="handleRowCommand($event, record)" /></div>
        </template>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" :page-size-options="['10', '20', '30', '50']" :show-total="count => `共 ${count} 条`" show-size-changer show-quick-jumper responsive @change="getList" />

    <ResourceDrawer :open="open" :title="title" :width="500" destroy-on-close @close="cancel">
      <a-form ref="configRef" :model="form" :rules="rules" layout="vertical">
        <a-form-item label="参数名称" name="configName"><a-input v-model:value="form.configName" placeholder="请输入参数名称" /></a-form-item>
        <a-form-item label="参数键名" name="configKey"><a-input v-model:value="form.configKey" placeholder="请输入参数键名" /></a-form-item>
        <a-form-item label="参数键值" name="configValue"><a-textarea v-model:value="form.configValue" placeholder="请输入参数键值" /></a-form-item>
        <a-form-item label="系统内置" name="configType"><a-radio-group v-model:value="form.configType" :options="yesNoOptions" /></a-form-item>
        <a-form-item label="备注" name="remark"><a-textarea v-model:value="form.remark" placeholder="请输入内容" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="cancel">取 消</a-button><a-button type="primary" @click="submitForm">确 定</a-button></template>
    </ResourceDrawer>
  </WorkspacePage>
</template>

<script setup name="Config">
import { useWindowSize } from '@vueuse/core'
import { DeleteOutlined, DownloadOutlined, EditOutlined, FilterOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, SyncOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import { checkPermi } from '@/utils/permission'
import { listConfig, getConfig, delConfig, addConfig, updateConfig, refreshCache } from "@/api/system/config";

const { proxy } = getCurrentInstance();
const { sys_yes_no } = proxy.useDict("sys_yes_no");

const configList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");
const dateRange = ref([]);
const queryRef = ref();
const configRef = ref();
const yesNoOptions = computed(() => sys_yes_no.value.map(item => ({ label: item.label, value: item.value })));
const { width: viewportWidth } = useWindowSize();
const columns = computed(() => [
  { key: 'selection', width: 55, align: 'center' },
  { title: '参数主键', dataIndex: 'configId', key: 'configId', align: 'center' },
  { title: '参数名称', dataIndex: 'configName', key: 'configName', ellipsis: true, align: 'center' },
  { title: '参数键名', dataIndex: 'configKey', key: 'configKey', ellipsis: true, align: 'center' },
  { title: '参数键值', dataIndex: 'configValue', key: 'configValue', ellipsis: true, align: 'center' },
  { title: '系统内置', key: 'configType', align: 'center' },
  { title: '备注', dataIndex: 'remark', key: 'remark', ellipsis: true, align: 'center' },
  { title: '创建时间', key: 'createTime', width: 180, align: 'center' },
  { title: '操作', key: 'actions', width: viewportWidth.value <= 767 ? 64 : 150, align: 'center' },
]);
const configRowActions = computed(() => [
  checkPermi(['system:config:edit']) && { command: 'edit', label: '修改', icon: EditOutlined },
  checkPermi(['system:config:remove']) && { command: 'delete', label: '删除', icon: DeleteOutlined, danger: true, divided: true },
].filter(Boolean));
const allSelected = computed(() => configList.value.length > 0 && ids.value.length === configList.value.length);
const partiallySelected = computed(() => ids.value.length > 0 && !allSelected.value);

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    configName: undefined,
    configKey: undefined,
    configType: undefined
  },
  rules: {
    configName: [{ required: true, message: "参数名称不能为空", trigger: "blur" }],
    configKey: [{ required: true, message: "参数键名不能为空", trigger: "blur" }],
    configValue: [{ required: true, message: "参数键值不能为空", trigger: "blur" }]
  }
});

const { queryParams, form, rules } = toRefs(data);

/** 查询参数列表 */
function getList() {
  loading.value = true;
  listConfig(proxy.addDateRange(queryParams.value, dateRange.value)).then(response => {
    configList.value = response.rows;
    total.value = response.total;
    handleSelectionChange([]);
    loading.value = false;
  });
}
/** 取消按钮 */
function cancel() {
  open.value = false;
  reset();
}
/** 表单重置 */
function reset() {
  form.value = {
    configId: undefined,
    configName: undefined,
    configKey: undefined,
    configValue: undefined,
    configType: "Y",
    remark: undefined
  };
  nextTick(() => configRef.value?.clearValidate());
}
/** 搜索按钮操作 */
function handleQuery() {
  queryParams.value.pageNum = 1;
  getList();
}
/** 重置按钮操作 */
function resetQuery() {
  dateRange.value = [];
  queryRef.value?.resetFields();
  handleQuery();
}
/** 多选框选中数据 */
function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.configId);
  single.value = selection.length != 1;
  multiple.value = !selection.length;
}
function toggleRow(row, checked) {
  const selected = checked ? [...configList.value.filter(item => ids.value.includes(item.configId)), row] : configList.value.filter(item => ids.value.includes(item.configId) && item.configId !== row.configId);
  handleSelectionChange([...new Map(selected.map(item => [item.configId, item])).values()]);
}
function toggleAll(event) { handleSelectionChange(event.target.checked ? configList.value : []); }
/** 新增按钮操作 */
function handleAdd() {
  reset();
  open.value = true;
  title.value = "添加参数";
}
/** 修改按钮操作 */
function handleUpdate(row) {
  reset();
  const configId = row.configId || ids.value;
  getConfig(configId).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "修改参数";
  });
}
/** 提交按钮 */
async function submitForm() {
  const valid = await configRef.value.validateFields().then(() => true).catch(() => false);
  if (valid) {
      if (form.value.configId != undefined) {
        updateConfig(form.value).then(response => {
          feedback.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addConfig(form.value).then(response => {
          feedback.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
  }
}
/** 删除按钮操作 */
function handleDelete(row) {
  const configIds = row.configId || ids.value;
  feedback.confirm('是否确认删除参数编号为"' + configIds + '"的数据项？').then(function () {
    return delConfig(configIds);
  }).then(() => {
    getList();
    feedback.msgSuccess("删除成功");
  }).catch(() => {});
}
function handleRowCommand(command, row) {
  const actions = { edit: handleUpdate, delete: handleDelete };
  actions[command]?.(row);
}
/** 导出按钮操作 */
function handleExport() {
  proxy.download("system/config/export", {
    ...queryParams.value
  }, `config_${new Date().getTime()}.xlsx`);
}
/** 刷新缓存按钮操作 */
function handleRefreshCache() {
  refreshCache().then(() => {
    feedback.msgSuccess("刷新缓存成功");
  });
}

getList();
</script>

<style scoped>
.query-form { margin-bottom: 16px; gap: 8px 0; }
.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 220px; }
:deep(.ant-pagination) { justify-content: flex-end; margin-top: 16px; }
.desktop-row-actions { display: inline-flex; }.mobile-row-actions { display: none; }
@media (max-width: 767px) { .query-form { display: grid; } .query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select), .query-form :deep(.ant-picker) { width: 100%; } :deep(.ant-pagination-options), :deep(.ant-pagination-total-text) { display: none; }.desktop-row-actions { display: none; }.mobile-row-actions { display: flex; justify-content: center; } }
</style>
