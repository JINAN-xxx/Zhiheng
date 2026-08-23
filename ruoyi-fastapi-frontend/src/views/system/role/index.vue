<template>
   <WorkspacePage title="角色管理" subtitle="管理系统角色、权限字符与数据范围">
      <a-form v-show="showSearch" ref="queryRef" :model="queryParams" layout="inline" class="query-form">
         <a-form-item label="角色名称" name="roleName"><a-input v-model:value="queryParams.roleName" allow-clear placeholder="请输入角色名称" style="width: 220px" @press-enter="handleQuery" /></a-form-item>
         <a-form-item label="权限字符" name="roleKey"><a-input v-model:value="queryParams.roleKey" allow-clear placeholder="请输入权限字符" style="width: 220px" @press-enter="handleQuery" /></a-form-item>
         <a-form-item label="状态" name="status"><a-select v-model:value="queryParams.status" allow-clear placeholder="角色状态" style="width: 180px" :options="statusOptions" /></a-form-item>
         <a-form-item label="创建时间"><a-range-picker v-model:value="dateRange" value-format="YYYY-MM-DD" /></a-form-item>
         <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
      </a-form>

      <div class="table-toolbar mb8">
         <a-button type="primary" @click="handleAdd" v-hasPermi="['system:role:add']"><PlusOutlined />新增</a-button>
         <a-button :disabled="single" @click="handleUpdate" v-hasPermi="['system:role:edit']"><EditOutlined />修改</a-button>
         <a-button danger :disabled="multiple" @click="handleDelete" v-hasPermi="['system:role:remove']"><DeleteOutlined />删除</a-button>
         <a-button @click="handleExport" v-hasPermi="['system:role:export']"><DownloadOutlined />导出</a-button>
         <span class="toolbar-spacer" />
         <a-tooltip title="显示或隐藏筛选"><a-button aria-label="显示或隐藏筛选" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
         <a-tooltip title="刷新"><a-button aria-label="刷新" :loading="loading" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
      </div>

      <ResourceTable :columns="roleColumns" :rows="roleList" :loading="loading" :pagination="false" row-key="roleId" :scroll="{ x: 900 }">
         <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
         <template #bodyCell="{ column, record: row }">
            <template v-if="column.key === 'selection'"><a-checkbox :checked="ids.includes(row.roleId)" @change="() => toggleRoleSelection(row)" /></template>
            <template v-else-if="column.key === 'status'"><a-switch v-model:checked="row.status" checked-value="0" un-checked-value="1" @change="handleStatusChange(row)" /></template>
            <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template>
            <template v-else-if="column.key === 'actions' && row.roleId !== 1">
               <a-space class="desktop-row-actions" :size="2">
                  <a-tooltip title="修改"><a-button type="link" aria-label="修改" @click="handleUpdate(row)" v-hasPermi="['system:role:edit']"><EditOutlined /></a-button></a-tooltip>
                  <a-tooltip title="删除"><a-button type="link" danger aria-label="删除" @click="handleDelete(row)" v-hasPermi="['system:role:remove']"><DeleteOutlined /></a-button></a-tooltip>
                  <a-tooltip title="数据权限"><a-button type="link" aria-label="数据权限" @click="handleDataScope(row)" v-hasPermi="['system:role:edit']"><SafetyCertificateOutlined /></a-button></a-tooltip>
                  <a-tooltip title="分配用户"><a-button type="link" aria-label="分配用户" @click="handleAuthUser(row)" v-hasPermi="['system:role:edit']"><TeamOutlined /></a-button></a-tooltip>
               </a-space>
               <WorkspaceMoreMenu v-if="roleRowMenuItems(row).length" class="mobile-row-actions" :items="roleRowMenuItems(row)" @command="handleCommand($event, row)" />
            </template>
         </template>
      </ResourceTable>
      <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" show-size-changer @change="getList" />

      <a-modal v-model:open="open" :title="title" :width="560">
         <a-form ref="roleRef" :model="form" :rules="rules" layout="vertical">
            <a-form-item label="角色名称" name="roleName"><a-input v-model:value="form.roleName" placeholder="请输入角色名称" /></a-form-item>
            <a-form-item name="roleKey">
               <template #label><span>权限字符 <a-tooltip title="控制器中定义的权限字符，如：RoleInterfaceAuthDependency('admin')"><QuestionCircleOutlined /></a-tooltip></span></template>
               <a-input v-model:value="form.roleKey" placeholder="请输入权限字符" />
            </a-form-item>
            <a-form-item label="角色顺序" name="roleSort"><a-input-number v-model:value="form.roleSort" :min="0" style="width: 100%" /></a-form-item>
            <a-form-item label="状态"><a-radio-group v-model:value="form.status"><a-radio v-for="dict in sys_normal_disable" :key="dict.value" :value="dict.value">{{ dict.label }}</a-radio></a-radio-group></a-form-item>
            <a-form-item label="菜单权限">
               <a-space wrap class="tree-controls">
                  <a-checkbox v-model:checked="menuExpand" @change="handleCheckedTreeExpand(menuExpand, 'menu')">展开/折叠</a-checkbox>
                  <a-checkbox v-model:checked="menuNodeAll" @change="handleCheckedTreeNodeAll(menuNodeAll, 'menu')">全选/全不选</a-checkbox>
                  <a-checkbox v-model:checked="form.menuCheckStrictly" @change="handleCheckedTreeConnect(form.menuCheckStrictly, 'menu')">父子联动</a-checkbox>
               </a-space>
               <a-tree class="tree-border" checkable :tree-data="menuOptions" :field-names="treeFieldNames" :checked-keys="treeCheckedValue(menuCheckedKeys, !form.menuCheckStrictly)" :expanded-keys="menuExpandedKeys" :check-strictly="!form.menuCheckStrictly" @check="(keys, info) => handleTreeCheck(keys, info, 'menu')" @expand="(keys) => menuExpandedKeys = keys" />
            </a-form-item>
            <a-form-item label="备注"><a-textarea v-model:value="form.remark" placeholder="请输入内容" /></a-form-item>
         </a-form>
         <template #footer><a-button @click="cancel">取消</a-button><a-button type="primary" @click="submitForm">确定</a-button></template>
      </a-modal>

      <a-modal v-model:open="openDataScope" :title="title" :width="560">
         <a-form :model="form" layout="vertical">
            <a-form-item label="角色名称"><a-input v-model:value="form.roleName" disabled /></a-form-item>
            <a-form-item label="权限字符"><a-input v-model:value="form.roleKey" disabled /></a-form-item>
            <a-form-item label="权限范围"><a-select v-model:value="form.dataScope" :options="dataScopeOptions" @change="dataScopeSelectChange" /></a-form-item>
            <a-form-item v-show="form.dataScope == 2" label="数据权限">
               <a-space wrap class="tree-controls">
                  <a-checkbox v-model:checked="deptExpand" @change="handleCheckedTreeExpand(deptExpand, 'dept')">展开/折叠</a-checkbox>
                  <a-checkbox v-model:checked="deptNodeAll" @change="handleCheckedTreeNodeAll(deptNodeAll, 'dept')">全选/全不选</a-checkbox>
                  <a-checkbox v-model:checked="form.deptCheckStrictly" @change="handleCheckedTreeConnect(form.deptCheckStrictly, 'dept')">父子联动</a-checkbox>
               </a-space>
               <a-tree class="tree-border" checkable :tree-data="deptOptions" :field-names="treeFieldNames" :checked-keys="treeCheckedValue(deptCheckedKeys, !form.deptCheckStrictly)" :expanded-keys="deptExpandedKeys" :check-strictly="!form.deptCheckStrictly" @check="(keys, info) => handleTreeCheck(keys, info, 'dept')" @expand="(keys) => deptExpandedKeys = keys" />
            </a-form-item>
         </a-form>
         <template #footer><a-button @click="cancelDataScope">取消</a-button><a-button type="primary" @click="submitDataScope">确定</a-button></template>
      </a-modal>
   </WorkspacePage>
</template>

<script setup name="Role">
import {
  DeleteOutlined, DownloadOutlined, EditOutlined, FilterOutlined, PlusOutlined, QuestionCircleOutlined,
  ReloadOutlined, SafetyCertificateOutlined, SearchOutlined, TeamOutlined,
} from '@ant-design/icons-vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
import feedback from '@/plugins/antdFeedback'
import { checkPermi } from '@/utils/permission'
import { convertTreeSelectionMode, normalizeStoredTreeSelection } from '@/utils/treeSelection'
import { addRole, changeRoleStatus, dataScope, delRole, getRole, listRole, updateRole, deptTreeSelect } from "@/api/system/role";
import { roleMenuTreeselect, treeselect as menuTreeselect } from "@/api/system/menu";

const router = useRouter();
const { proxy } = getCurrentInstance();
const { sys_normal_disable } = proxy.useDict("sys_normal_disable");

const roleList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");
const dateRange = ref([]);
const menuOptions = ref([]);
const menuExpand = ref(false);
const menuNodeAll = ref(false);
const deptExpand = ref(true);
const deptNodeAll = ref(false);
const deptOptions = ref([]);
const openDataScope = ref(false);
const queryRef = ref();
const roleRef = ref();
const menuCheckedKeys = ref([]);
const menuHalfCheckedKeys = ref([]);
const menuExpandedKeys = ref([]);
const deptCheckedKeys = ref([]);
const deptHalfCheckedKeys = ref([]);
const deptExpandedKeys = ref([]);
const treeFieldNames = { title: "label", key: "id", children: "children" };

/** 数据范围选项*/
const dataScopeOptions = ref([
  { value: "1", label: "全部数据权限" },
  { value: "2", label: "自定数据权限" },
  { value: "3", label: "本部门数据权限" },
  { value: "4", label: "本部门及以下数据权限" },
  { value: "5", label: "仅本人数据权限" }
]);
const statusOptions = computed(() => sys_normal_disable.value.map(dict => ({ label: dict.label, value: dict.value })));
const allSelected = computed(() => roleList.value.length > 0 && roleList.value.every((item) => ids.value.includes(item.roleId)));
const someSelected = computed(() => ids.value.length > 0 && !allSelected.value);
const roleColumns = [
  { title: "", key: "selection", width: 48 },
  { title: "角色编号", dataIndex: "roleId", key: "roleId", width: 120 },
  { title: "角色名称", dataIndex: "roleName", key: "roleName", width: 150, ellipsis: true },
  { title: "权限字符", dataIndex: "roleKey", key: "roleKey", width: 150, ellipsis: true },
  { title: "显示顺序", dataIndex: "roleSort", key: "roleSort", width: 100 },
  { title: "状态", key: "status", width: 90 },
  { title: "创建时间", dataIndex: "createTime", key: "createTime", width: 180, mobileHidden: true },
  { title: "操作", key: "actions" },
];

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    roleName: undefined,
    roleKey: undefined,
    status: undefined
  },
  rules: {
    roleName: [{ required: true, message: "角色名称不能为空", trigger: "blur" }],
    roleKey: [{ required: true, message: "权限字符不能为空", trigger: "blur" }],
    roleSort: [{ required: true, message: "角色顺序不能为空", trigger: "blur" }]
  },
});

const { queryParams, form, rules } = toRefs(data);

/** 查询角色列表 */
function getList() {
  loading.value = true;
  listRole(proxy.addDateRange(queryParams.value, dateRange.value)).then(response => {
    roleList.value = response.rows;
    total.value = response.total;
    loading.value = false;
    handleSelectionChange([]);
  });
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
/** 删除按钮操作 */
function handleDelete(row) {
  const roleIds = row.roleId || ids.value;
  feedback.confirm('是否确认删除角色编号为"' + roleIds + '"的数据项?').then(function () {
    return delRole(roleIds);
  }).then(() => {
    getList();
    feedback.msgSuccess("删除成功");
  }).catch(() => {});
}
/** 导出按钮操作 */
function handleExport() {
  proxy.download("system/role/export", {
    ...queryParams.value,
  }, `role_${new Date().getTime()}.xlsx`);
}
/** 多选框选中数据 */
function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.roleId);
  single.value = selection.length != 1;
  multiple.value = !selection.length;
}
function toggleRoleSelection(row) {
  const index = ids.value.indexOf(row.roleId);
  if (index >= 0) ids.value.splice(index, 1);
  else ids.value.push(row.roleId);
  single.value = ids.value.length != 1;
  multiple.value = !ids.value.length;
}
function toggleAll(event) {
  handleSelectionChange(event.target.checked ? roleList.value : []);
}
/** 角色状态修改 */
function handleStatusChange(row) {
  let text = row.status === "0" ? "启用" : "停用";
  feedback.confirm('确认要"' + text + '""' + row.roleName + '"角色吗?').then(function () {
    return changeRoleStatus(row.roleId, row.status);
  }).then(() => {
    feedback.msgSuccess(text + "成功");
  }).catch(function () {
    row.status = row.status === "0" ? "1" : "0";
  });
}
/** 更多操作 */
function handleCommand(command, row) {
  switch (command) {
    case "handleUpdate":
      handleUpdate(row);
      break;
    case "handleDelete":
      handleDelete(row);
      break;
    case "handleDataScope":
      handleDataScope(row);
      break;
    case "handleAuthUser":
      handleAuthUser(row);
      break;
    default:
      break;
  }
}
function roleRowMenuItems(row) {
  if (row.roleId === 1) return [];
  return [
    checkPermi(["system:role:edit"]) && { command: "handleUpdate", label: "修改", icon: EditOutlined },
    checkPermi(["system:role:remove"]) && { command: "handleDelete", label: "删除", icon: DeleteOutlined, danger: true },
    checkPermi(["system:role:edit"]) && { command: "handleDataScope", label: "数据权限", icon: SafetyCertificateOutlined },
    checkPermi(["system:role:edit"]) && { command: "handleAuthUser", label: "分配用户", icon: TeamOutlined },
  ].filter(Boolean);
}
/** 分配用户 */
function handleAuthUser(row) {
  router.push("/system/role-auth/user/" + row.roleId);
}
/** 查询菜单树结构 */
function getMenuTreeselect() {
  menuTreeselect().then(response => {
    menuOptions.value = response.data;
  });
}
/** 所有部门节点数据 */
function getDeptAllCheckedKeys() {
  let checkedKeys = [...deptCheckedKeys.value];
  let halfCheckedKeys = [...deptHalfCheckedKeys.value];
  checkedKeys.unshift.apply(checkedKeys, halfCheckedKeys);
  return checkedKeys;
}
/** 重置新增的表单以及其他数据  */
function reset() {
  menuCheckedKeys.value = [];
  menuHalfCheckedKeys.value = [];
  menuExpandedKeys.value = [];
  deptCheckedKeys.value = [];
  deptHalfCheckedKeys.value = [];
  deptExpandedKeys.value = [];
  menuExpand.value = false;
  menuNodeAll.value = false;
  deptExpand.value = true;
  deptNodeAll.value = false;
  form.value = {
    roleId: undefined,
    roleName: undefined,
    roleKey: undefined,
    roleSort: 0,
    status: "0",
    menuIds: [],
    deptIds: [],
    menuCheckStrictly: true,
    deptCheckStrictly: true,
    remark: undefined
  };
  roleRef.value?.clearValidate();
}
/** 添加角色 */
function handleAdd() {
  reset();
  getMenuTreeselect();
  open.value = true;
  title.value = "添加角色";
}
/** 修改角色 */
function handleUpdate(row) {
  reset();
  const roleId = row.roleId || ids.value;
  const roleMenu = getRoleMenuTreeselect(roleId);
  getRole(roleId).then(response => {
    form.value = response.data;
    form.value.roleSort = Number(form.value.roleSort);
    open.value = true;
    roleMenu.then((res) => {
      const selection = normalizeStoredTreeSelection(menuOptions.value, res.checkedKeys, form.value.menuCheckStrictly);
      menuCheckedKeys.value = selection.checkedKeys;
      menuHalfCheckedKeys.value = selection.halfCheckedKeys;
    });
  });
  title.value = "修改角色";
}
/** 根据角色ID查询菜单树结构 */
function getRoleMenuTreeselect(roleId) {
  return roleMenuTreeselect(roleId).then(response => {
    menuOptions.value = response.menus;
    return response;
  });
}
/** 根据角色ID查询部门树结构 */
function getDeptTree(roleId) {
  return deptTreeSelect(roleId).then(response => {
    deptOptions.value = response.depts;
    return response;
  });
}
/** 树权限（展开/折叠）*/
function handleCheckedTreeExpand(value, type) {
  const target = type == "menu" ? menuExpandedKeys : deptExpandedKeys;
  const tree = type == "menu" ? menuOptions.value : deptOptions.value;
  target.value = value ? collectTreeKeys(tree) : [];
}
/** 树权限（全选/全不选） */
function handleCheckedTreeNodeAll(value, type) {
  const target = type == "menu" ? menuCheckedKeys : deptCheckedKeys;
  const tree = type == "menu" ? menuOptions.value : deptOptions.value;
  target.value = value ? collectTreeKeys(tree) : [];
  if (type == "menu") menuHalfCheckedKeys.value = [];
  else deptHalfCheckedKeys.value = [];
}
/** 树权限（父子联动） */
function handleCheckedTreeConnect(value, type) {
  const checked = type == "menu" ? menuCheckedKeys : deptCheckedKeys;
  const halfChecked = type == "menu" ? menuHalfCheckedKeys : deptHalfCheckedKeys;
  const tree = type == "menu" ? menuOptions.value : deptOptions.value;
  const selection = convertTreeSelectionMode(tree, checked.value, halfChecked.value, value);
  checked.value = selection.checkedKeys;
  halfChecked.value = selection.halfCheckedKeys;
  if (type == "menu") {
    form.value.menuCheckStrictly = value;
  } else if (type == "dept") {
    form.value.deptCheckStrictly = value;
  }
}
/** 所有菜单节点数据 */
function getMenuAllCheckedKeys() {
  let checkedKeys = [...menuCheckedKeys.value];
  let halfCheckedKeys = [...menuHalfCheckedKeys.value];
  checkedKeys.unshift.apply(checkedKeys, halfCheckedKeys);
  return checkedKeys;
}
/** 提交按钮 */
function submitForm() {
  roleRef.value.validateFields().then(() => {
      if (form.value.roleId != undefined) {
        form.value.menuIds = getMenuAllCheckedKeys();
        updateRole(form.value).then(() => {
          feedback.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        form.value.menuIds = getMenuAllCheckedKeys();
        addRole(form.value).then(() => {
          feedback.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
  }).catch(() => {});
}
/** 取消按钮 */
function cancel() {
  open.value = false;
  reset();
}
/** 选择角色权限范围触发 */
function dataScopeSelectChange(value) {
  if (value !== "2") {
    deptCheckedKeys.value = [];
    deptHalfCheckedKeys.value = [];
  }
}
/** 分配数据权限操作 */
function handleDataScope(row) {
  reset();
  const deptTreeSelect = getDeptTree(row.roleId);
  getRole(row.roleId).then(response => {
    form.value = response.data;
    openDataScope.value = true;
    deptTreeSelect.then(res => {
      const selection = normalizeStoredTreeSelection(deptOptions.value, res.checkedKeys, form.value.deptCheckStrictly);
      deptCheckedKeys.value = selection.checkedKeys;
      deptHalfCheckedKeys.value = selection.halfCheckedKeys;
      deptExpandedKeys.value = collectTreeKeys(deptOptions.value);
    });
  });
  title.value = "分配数据权限";
}
/** 提交按钮（数据权限） */
function submitDataScope() {
  if (form.value.roleId != undefined) {
    form.value.deptIds = getDeptAllCheckedKeys();
    dataScope(form.value).then(() => {
      feedback.msgSuccess("修改成功");
      openDataScope.value = false;
      getList();
    });
  }
}
/** 取消按钮（数据权限）*/
function cancelDataScope() {
  openDataScope.value = false;
  reset();
}

function collectTreeKeys(nodes) {
  const keys = [];
  const visit = (items) => items.forEach(item => {
    keys.push(item.id);
    if (Array.isArray(item.children)) visit(item.children);
  });
  visit(Array.isArray(nodes) ? nodes : []);
  return keys;
}

function treeCheckedValue(keys, strict) {
  return strict ? { checked: keys, halfChecked: [] } : keys;
}

function handleTreeCheck(keys, info, type) {
  const checked = Array.isArray(keys) ? keys : keys.checked || [];
  const halfChecked = Array.isArray(keys) ? info.halfCheckedKeys || [] : keys.halfChecked || [];
  if (type == "menu") {
    menuCheckedKeys.value = [...checked];
    menuHalfCheckedKeys.value = [...halfChecked];
  } else {
    deptCheckedKeys.value = [...checked];
    deptHalfCheckedKeys.value = [...halfChecked];
  }
}

getList();
</script>

<style scoped>
.query-form { margin-bottom: 16px; row-gap: 8px; }
.table-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.toolbar-spacer { flex: 1 1 auto; }
.tree-controls { margin-bottom: 10px; }
.tree-border { max-height: 320px; overflow: auto; border: 1px solid var(--workspace-border); border-radius: var(--workspace-radius-sm); padding: 8px; }
:deep(.ant-pagination) { margin-top: 16px; }
.mobile-row-actions { display: none; }
@media (max-width: 767px) {
  .desktop-row-actions { display: none; }
  .mobile-row-actions { display: inline-flex; }
}
</style>
