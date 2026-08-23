<template>
  <WorkspacePage title="部门管理" subtitle="组织层级、负责人和状态">
    <template #actions><a-space wrap>
      <a-button type="primary" @click="handleAdd" v-hasPermi="['system:dept:add']"><PlusOutlined />新增</a-button>
      <a-button @click="handleSaveSort" v-hasPermi="['system:dept:edit']"><SaveOutlined />保存排序</a-button>
      <a-button @click="toggleExpandAll"><NodeExpandOutlined />展开/折叠</a-button>
      <a-tooltip title="显示/隐藏搜索"><a-button shape="circle" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
      <a-tooltip title="刷新"><a-button shape="circle" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
    </a-space></template>
    <a-form v-show="showSearch" ref="queryRef" :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="部门名称" name="deptName"><a-input v-model:value="queryParams.deptName" allow-clear placeholder="请输入部门名称" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="状态" name="status"><a-select v-model:value="queryParams.status" allow-clear placeholder="部门状态" :options="normalDisableOptions" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>
    <a-table v-if="refreshTable" :columns="columns" :data-source="deptList" :loading="loading" :pagination="false" row-key="deptId" :expanded-row-keys="expandedRowKeys" :scroll="{ x: 900 }" @expanded-rows-change="expandedRowKeys = $event">
      <template #bodyCell="{ column, record }">
        <a-input-number v-if="column.key === 'orderNum'" v-model:value="record.orderNum" :min="0" class="sort-input" />
        <dict-tag v-else-if="column.key === 'status'" :options="sys_normal_disable" :value="record.status" />
        <span v-else-if="column.key === 'createTime'">{{ parseTime(record.createTime) }}</span>
        <a-space v-else-if="column.key === 'actions'">
          <a-button type="link" @click="handleUpdate(record)" v-hasPermi="['system:dept:edit']"><EditOutlined />修改</a-button>
          <a-button type="link" @click="handleAdd(record)" v-hasPermi="['system:dept:add']"><PlusOutlined />新增</a-button>
          <a-button v-if="record.parentId != 0" type="link" danger @click="handleDelete(record)" v-hasPermi="['system:dept:remove']"><DeleteOutlined />删除</a-button>
        </a-space>
      </template>
    </a-table>
    <ResourceDrawer :open="open" :title="title" :width="600" destroy-on-close @close="cancel">
      <a-form ref="deptRef" :model="form" :rules="rules" layout="vertical">
        <a-row :gutter="16">
          <a-col v-if="form.parentId !== 0" :span="24"><a-form-item label="上级部门" name="parentId"><a-tree-select v-model:value="form.parentId" :tree-data="deptOptions" :field-names="deptFieldNames" tree-default-expand-all placeholder="选择上级部门" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="部门名称" name="deptName"><a-input v-model:value="form.deptName" placeholder="请输入部门名称" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="显示排序" name="orderNum"><a-input-number v-model:value="form.orderNum" :min="0" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="负责人" name="leader"><a-input v-model:value="form.leader" :maxlength="20" placeholder="请输入负责人" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="联系电话" name="phone"><a-input v-model:value="form.phone" :maxlength="11" placeholder="请输入联系电话" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="邮箱" name="email"><a-input v-model:value="form.email" :maxlength="50" placeholder="请输入邮箱" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="部门状态"><a-radio-group v-model:value="form.status" :options="normalDisableOptions" /></a-form-item></a-col>
        </a-row>
      </a-form>
      <template #footer><a-button @click="cancel">取 消</a-button><a-button type="primary" @click="submitForm">确 定</a-button></template>
    </ResourceDrawer>
  </WorkspacePage>
</template>

<script setup name="Dept">
import { DeleteOutlined, EditOutlined, FilterOutlined, NodeExpandOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import { listDept, getDept, delDept, addDept, updateDept, updateDeptSort, listDeptExcludeChild } from "@/api/system/dept";

const { proxy } = getCurrentInstance();
const { sys_normal_disable } = proxy.useDict("sys_normal_disable");

const deptList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const title = ref("");
const deptOptions = ref([]);
const isExpandAll = ref(true);
const refreshTable = ref(true);
const originalOrders = ref({});
const expandedRowKeys = ref([]);
const queryRef = ref();
const deptRef = ref();
const normalDisableOptions = computed(() => sys_normal_disable.value.map(item => ({ label: item.label, value: item.value })));
const deptFieldNames = { value: 'deptId', label: 'deptName', children: 'children' };
const columns = [
  { title: '部门名称', dataIndex: 'deptName', key: 'deptName', width: 260 }, { title: '排序', key: 'orderNum', width: 120 },
  { title: '状态', key: 'status', width: 100 }, { title: '创建时间', key: 'createTime', width: 200, align: 'center' },
  { title: '操作', key: 'actions', width: 230, align: 'center' },
];
const collectDeptKeys = list => list.flatMap(item => [item.deptId, ...collectDeptKeys(item.children || [])]);

const data = reactive({
  form: {},
  queryParams: {
    deptName: undefined,
    status: undefined
  },
  rules: {
    parentId: [{ required: true, message: "上级部门不能为空", trigger: "blur" }],
    deptName: [{ required: true, message: "部门名称不能为空", trigger: "blur" }],
    orderNum: [{ required: true, message: "显示排序不能为空", trigger: "blur" }],
    email: [{ type: "email", message: "请输入正确的邮箱地址", trigger: ["blur", "change"] }],
    phone: [{ pattern: /^1[3|4|5|6|7|8|9][0-9]\d{8}$/, message: "请输入正确的手机号码", trigger: "blur" }]
  },
});

const { queryParams, form, rules } = toRefs(data);

/** 查询部门列表 */
function getList() {
  loading.value = true;
  listDept(queryParams.value).then(response => {
    deptList.value = proxy.handleTree(response.data, "deptId");
    const availableKeys = collectDeptKeys(deptList.value);
    expandedRowKeys.value = isExpandAll.value ? availableKeys : expandedRowKeys.value.filter(key => availableKeys.includes(key));
    recordOriginalOrders(deptList.value);
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
    deptId: undefined,
    parentId: undefined,
    deptName: undefined,
    orderNum: 0,
    leader: undefined,
    phone: undefined,
    email: undefined,
    status: "0"
  };
  nextTick(() => deptRef.value?.clearValidate());
}
/** 搜索按钮操作 */
function handleQuery() {
  getList();
}
/** 重置按钮操作 */
function resetQuery() {
  queryRef.value?.resetFields();
  handleQuery();
}
/** 新增按钮操作 */
function handleAdd(row) {
  reset();
  listDept().then(response => {
    deptOptions.value = proxy.handleTree(response.data, "deptId");
  });
  if (row != undefined) {
    form.value.parentId = row.deptId;
  }
  open.value = true;
  title.value = "添加部门";
}
/** 展开/折叠操作 */
function toggleExpandAll() {
  refreshTable.value = false;
  isExpandAll.value = !isExpandAll.value;
  expandedRowKeys.value = isExpandAll.value ? collectDeptKeys(deptList.value) : [];
  nextTick(() => {
    refreshTable.value = true;
  });
}
/** 修改按钮操作 */
function handleUpdate(row) {
  reset();
  listDeptExcludeChild(row.deptId).then(response => {
    deptOptions.value = proxy.handleTree(response.data, "deptId");
  });
  getDept(row.deptId).then(response => {
    form.value = response.data;
    open.value = true;
    title.value = "修改部门";
  });
}
/** 提交按钮 */
async function submitForm() {
  const valid = await deptRef.value.validateFields().then(() => true).catch(() => false);
  if (valid) {
      if (form.value.deptId != undefined) {
        updateDept(form.value).then(response => {
          feedback.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addDept(form.value).then(response => {
          feedback.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
  }
}

/** 递归记录原始排序 */
function recordOriginalOrders(list) {
  list.forEach(item => {
    originalOrders.value[item.deptId] = item.orderNum;
    if (item.children && item.children.length) {
      recordOriginalOrders(item.children);
    }
  });
}

/** 保存排序 */
function handleSaveSort() {
  const changedDeptIds = [];
  const changedOrderNums = [];
  const collectChanged = (list) => {
    list.forEach(item => {
      if (String(originalOrders.value[item.deptId]) !== String(item.orderNum)) {
        changedDeptIds.push(item.deptId);
        changedOrderNums.push(item.orderNum);
      }
      if (item.children && item.children.length) {
        collectChanged(item.children);
      }
    });
  };
  collectChanged(deptList.value);
  if (changedDeptIds.length === 0) {
    feedback.msgWarning("未检测到排序修改");
    return;
  }
  updateDeptSort({ deptIds: changedDeptIds.join(","), orderNums: changedOrderNums.join(",") }).then(() => {
    feedback.msgSuccess("排序保存成功");
    recordOriginalOrders(deptList.value);
  });
}

/** 删除按钮操作 */
function handleDelete(row) {
  feedback.confirm('是否确认删除名称为"' + row.deptName + '"的数据项?').then(function() {
    return delDept(row.deptId);
  }).then(() => {
    getList();
    feedback.msgSuccess("删除成功");
  }).catch(() => {});
}

getList();
</script>

<style scoped>
.query-form { margin-bottom: 16px; gap: 8px 0; }.query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 200px; }.sort-input { width: 88px; }
@media (max-width: 767px) { .query-form { display: grid; }.query-form :deep(.ant-form-item), .query-form :deep(.ant-input), .query-form :deep(.ant-select) { width: 100%; } }
</style>
