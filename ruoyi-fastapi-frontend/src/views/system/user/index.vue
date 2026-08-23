<template>
  <div class="app-container workspace-admin-page tree-sidebar-manage-wrap">
    <tree-panel v-if="!isMobile" ref="deptTreeRef" title="组织机构" :tree-data="deptOptions" search-placeholder="请输入部门名称" storage-key="dept-sidebar-width" :defaultExpandAll="true" @node-click="handleNodeClick" @refresh="getDeptTree" />
    <div class="tree-sidebar-content">
      <div class="content-inner">
        <div v-if="isMobile" class="mobile-dept-toolbar">
          <a-button block @click="mobileDeptOpen = true"><ApartmentOutlined />选择部门</a-button>
        </div>
        <a-form v-show="showSearch" ref="queryRef" :model="queryParams" layout="inline" class="query-form">
          <a-form-item label="用户名称" name="userName"><a-input v-model:value="queryParams.userName" allow-clear placeholder="请输入用户名称" style="width: 220px" @press-enter="handleQuery" /></a-form-item>
          <a-form-item label="手机号码" name="phonenumber"><a-input v-model:value="queryParams.phonenumber" allow-clear placeholder="请输入手机号码" style="width: 220px" @press-enter="handleQuery" /></a-form-item>
          <a-form-item label="状态" name="status"><a-select v-model:value="queryParams.status" allow-clear placeholder="用户状态" style="width: 180px" :options="normalStatusOptions" /></a-form-item>
          <a-form-item label="创建时间"><a-range-picker v-model:value="dateRange" value-format="YYYY-MM-DD" /></a-form-item>
          <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
        </a-form>

        <div class="table-toolbar mb8">
          <a-button type="primary" @click="handleAdd" v-hasPermi="['system:user:add']"><PlusOutlined />新增</a-button>
          <a-button :disabled="single" @click="handleUpdate" v-hasPermi="['system:user:edit']"><EditOutlined />修改</a-button>
          <a-button danger :disabled="multiple" @click="handleDelete" v-hasPermi="['system:user:remove']"><DeleteOutlined />删除</a-button>
          <a-button @click="handleImport" v-hasPermi="['system:user:import']"><UploadOutlined />导入</a-button>
          <a-button @click="handleExport" v-hasPermi="['system:user:export']"><DownloadOutlined />导出</a-button>
          <span class="toolbar-spacer" />
          <a-tooltip title="显示或隐藏筛选"><a-button aria-label="显示或隐藏筛选" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
          <a-dropdown :trigger="['click']">
            <a-tooltip title="列设置"><a-button aria-label="列设置"><SettingOutlined /></a-button></a-tooltip>
            <template #overlay><a-menu><a-menu-item v-for="(column, key) in columns" :key="key" @click="({ domEvent }) => domEvent.stopPropagation()"><a-checkbox v-model:checked="column.visible">{{ column.label }}</a-checkbox></a-menu-item></a-menu></template>
          </a-dropdown>
          <a-tooltip title="刷新"><a-button aria-label="刷新" :loading="loading" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
        </div>

        <ResourceTable :columns="userColumns" :rows="userList" :loading="loading" :pagination="false" row-key="userId" :scroll="{ x: 980 }">
          <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
          <template #bodyCell="{ column, record: row }">
            <template v-if="column.key === 'selection'"><a-checkbox :checked="ids.includes(row.userId)" @change="() => toggleUserSelection(row)" /></template>
            <template v-else-if="column.key === 'userName'"><a-button type="link" @click="handleViewData(row)">{{ row.userName }}</a-button></template>
            <template v-else-if="column.key === 'deptName'">{{ row.dept?.deptName }}</template>
            <template v-else-if="column.key === 'status'"><a-switch v-model:checked="row.status" checked-value="0" un-checked-value="1" @change="handleStatusChange(row)" /></template>
            <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template>
            <template v-else-if="column.key === 'actions' && row.userId !== 1">
              <a-space class="desktop-row-actions" :size="2">
                <a-tooltip title="修改"><a-button type="link" aria-label="修改" @click="handleUpdate(row)" v-hasPermi="['system:user:edit']"><EditOutlined /></a-button></a-tooltip>
                <a-tooltip title="删除"><a-button type="link" danger aria-label="删除" @click="handleDelete(row)" v-hasPermi="['system:user:remove']"><DeleteOutlined /></a-button></a-tooltip>
                <a-tooltip title="重置密码"><a-button type="link" aria-label="重置密码" @click="handleResetPwd(row)" v-hasPermi="['system:user:resetPwd']"><KeyOutlined /></a-button></a-tooltip>
                <a-tooltip title="分配角色"><a-button type="link" aria-label="分配角色" @click="handleAuthRole(row)" v-hasPermi="['system:user:edit']"><SafetyCertificateOutlined /></a-button></a-tooltip>
              </a-space>
              <WorkspaceMoreMenu v-if="userRowMenuItems(row).length" class="mobile-row-actions" :items="userRowMenuItems(row)" @command="handleCommand($event, row)" />
            </template>
          </template>
        </ResourceTable>
        <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" show-size-changer @change="getList" />
      </div>
    </div>

    <ResourceDrawer :open="mobileDeptOpen" title="选择部门" width="100%" @close="mobileDeptOpen = false">
      <tree-panel v-if="isMobile" ref="deptTreeRef" class="mobile-dept-tree" title="组织机构" :tree-data="deptOptions" search-placeholder="请输入部门名称" :enable-storage="false" :defaultExpandAll="true" @node-click="handleNodeClick" @refresh="getDeptTree" />
    </ResourceDrawer>

    <a-modal v-model:open="open" :title="title" :width="600">
      <a-form ref="userRef" :model="form" :rules="rules" layout="vertical">
        <a-row :gutter="16">
          <a-col :xs="24" :md="12"><a-form-item label="用户昵称" name="nickName"><a-input v-model:value="form.nickName" placeholder="请输入用户昵称" :maxlength="30" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="归属部门" name="deptId"><a-tree-select v-model:value="form.deptId" :tree-data="enabledDeptOptions" :field-names="{ value: 'id', label: 'label', children: 'children' }" placeholder="请选择归属部门" allow-clear tree-default-expand-all /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="手机号码" name="phonenumber"><a-input v-model:value="form.phonenumber" placeholder="请输入手机号码" :maxlength="11" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="邮箱" name="email"><a-input v-model:value="form.email" placeholder="请输入邮箱" :maxlength="50" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item v-if="form.userId == undefined" label="用户名称" name="userName"><a-input v-model:value="form.userName" placeholder="请输入用户名称" :maxlength="30" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item v-if="form.userId == undefined" label="用户密码" name="password" :rules="pwdValidator"><a-input-password v-model:value="form.password" placeholder="请输入用户密码" :maxlength="20" autocomplete="new-password" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="用户性别"><a-select v-model:value="form.sex" placeholder="请选择" :options="sexOptions" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="状态"><a-radio-group v-model:value="form.status"><a-radio v-for="dict in sys_normal_disable" :key="dict.value" :value="dict.value">{{ dict.label }}</a-radio></a-radio-group></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="岗位"><a-select v-model:value="form.postIds" mode="multiple" placeholder="请选择" :options="postSelectOptions" /></a-form-item></a-col>
          <a-col :xs="24" :md="12"><a-form-item label="角色"><a-select v-model:value="form.roleIds" mode="multiple" placeholder="请选择" :options="roleSelectOptions" /></a-form-item></a-col>
          <a-col :span="24"><a-form-item label="备注"><a-textarea v-model:value="form.remark" placeholder="请输入内容" /></a-form-item></a-col>
        </a-row>
      </a-form>
      <template #footer><a-button @click="cancel">取消</a-button><a-button type="primary" @click="submitForm">确定</a-button></template>
    </a-modal>

    <a-modal v-model:open="resetPasswordOpen" title="重置密码" :width="480" :mask-closable="false" destroy-on-close :after-close="clearResetPassword">
      <a-form ref="resetPasswordRef" :model="resetPasswordForm" :rules="resetPasswordRules" layout="vertical">
        <a-form-item :label="`「${resetPasswordUser?.userName || ''}」的新密码`" name="value"><a-input-password v-model:value="resetPasswordForm.value" autocomplete="new-password" /></a-form-item>
      </a-form>
      <template #footer><a-button @click="resetPasswordOpen = false">取消</a-button><a-button type="primary" @click="submitResetPassword">确定</a-button></template>
    </a-modal>

    <user-view-drawer ref="userViewRef" />
    <excel-import-dialog ref="importUserRef" title="用户导入" action="/system/user/importData" template-action="/system/user/importTemplate" template-file-name="user_template" update-support-label="是否更新已经存在的用户数据" @success="getList" />
  </div>
</template>

<script setup name="User">
import {
  ApartmentOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, FilterOutlined, KeyOutlined, PlusOutlined, ReloadOutlined,
  SafetyCertificateOutlined, SearchOutlined, SettingOutlined, UploadOutlined,
} from '@ant-design/icons-vue'
import { useWindowSize } from '@vueuse/core'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
import feedback from '@/plugins/antdFeedback'
import { checkPermi } from '@/utils/permission'
import TreePanel from "@/components/TreePanel";
import ExcelImportDialog from "@/components/ExcelImportDialog";
import UserViewDrawer from "./view";
import { usePasswordRule } from "@/utils/passwordRule";
import {
  changeUserStatus,
  listUser,
  resetUserPwd,
  delUser,
  getUser,
  updateUser,
  addUser,
  deptTreeSelect,
} from "@/api/system/user";

const router = useRouter();
const { proxy } = getCurrentInstance();
const { pwdValidator, pwdPromptValidator } = usePasswordRule();
const { sys_normal_disable, sys_user_sex } = proxy.useDict(
  "sys_normal_disable",
  "sys_user_sex"
);

const userList = ref([]);
const open = ref(false);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const single = ref(true);
const multiple = ref(true);
const total = ref(0);
const title = ref("");
const dateRange = ref([]);
const deptOptions = ref(undefined);
const enabledDeptOptions = ref(undefined);
const initPassword = ref(undefined);
const postOptions = ref([]);
const roleOptions = ref([]);
const queryRef = ref();
const userRef = ref();
const resetPasswordRef = ref();
const deptTreeRef = ref();
const userViewRef = ref();
const importUserRef = ref();
const mobileDeptOpen = ref(false);
const { width: viewportWidth } = useWindowSize();
const isMobile = computed(() => viewportWidth.value < 768);
const resetPasswordOpen = ref(false);
const resetPasswordUser = ref(null);
const resetPasswordForm = reactive({ value: "" });
// 列显隐信息

const columns = ref({
  userId: { label: "用户编号", visible: true },
  userName: { label: "用户名称", visible: true },
  nickName: { label: "用户昵称", visible: true },
  deptName: { label: "部门", visible: true },
  phonenumber: { label: "手机号码", visible: true },
  status: { label: "状态", visible: true },
  createTime: { label: "创建时间", visible: true },
});
const userColumns = computed(() => [
  { title: "", key: "selection", width: 48 },
  columns.value.userId.visible && { title: "用户编号", dataIndex: "userId", key: "userId", width: 100 },
  columns.value.userName.visible && { title: "用户名称", dataIndex: "userName", key: "userName", ellipsis: true },
  columns.value.nickName.visible && { title: "用户昵称", dataIndex: "nickName", key: "nickName", ellipsis: true },
  columns.value.deptName.visible && { title: "部门", key: "deptName", ellipsis: true },
  columns.value.phonenumber.visible && { title: "手机号码", dataIndex: "phonenumber", key: "phonenumber", width: 130 },
  columns.value.status.visible && { title: "状态", key: "status", width: 90 },
  columns.value.createTime.visible && { title: "创建时间", dataIndex: "createTime", key: "createTime", width: 170, mobileHidden: true },
  { title: "操作", key: "actions" },
].filter(Boolean));
const normalStatusOptions = computed(() => sys_normal_disable.value.map(dict => ({ label: dict.label, value: dict.value })));
const sexOptions = computed(() => sys_user_sex.value.map(dict => ({ label: dict.label, value: dict.value })));
const postSelectOptions = computed(() => postOptions.value.map(item => ({ label: item.postName, value: item.postId, disabled: item.status == 1 })));
const roleSelectOptions = computed(() => roleOptions.value.map(item => ({ label: item.roleName, value: item.roleId, disabled: item.status == 1 })));
const allSelected = computed(() => userList.value.length > 0 && userList.value.every((item) => ids.value.includes(item.userId)));
const someSelected = computed(() => ids.value.length > 0 && !allSelected.value);
const resetPasswordRules = {
  value: [{ validator: async (_, value) => {
    const error = pwdPromptValidator(value);
    if (error) throw new Error(error);
  }, trigger: "blur" }],
};

const data = reactive({
  form: {},
  queryParams: {
    pageNum: 1,
    pageSize: 10,
    userName: undefined,
    phonenumber: undefined,
    status: undefined,
    deptId: undefined,
  },
  rules: {
    userName: [
      { required: true, message: "用户名称不能为空", trigger: "blur" },
      {
        min: 2,
        max: 20,
        message: "用户名称长度必须介于 2 和 20 之间",
        trigger: "blur",
      },
    ],
    nickName: [
      { required: true, message: "用户昵称不能为空", trigger: "blur" },
    ],
    email: [
      {
        type: "email",
        message: "请输入正确的邮箱地址",
        trigger: ["blur", "change"],
      },
    ],
    phonenumber: [
      {
        pattern: /^1[3|4|5|6|7|8|9][0-9]\d{8}$/,
        message: "请输入正确的手机号码",
        trigger: "blur",
      },
    ],
  },
});

const { queryParams, form, rules } = toRefs(data);

/** 查询用户列表 */
function getList() {
  loading.value = true;
  listUser(proxy.addDateRange(queryParams.value, dateRange.value)).then(
    (res) => {
      loading.value = false;
      userList.value = res.rows;
      total.value = res.total;
      handleSelectionChange([]);
    }
  );
}
/** 查询部门下拉树结构 */
function getDeptTree() {
  deptTreeSelect().then((response) => {
    deptOptions.value = response.data;
    enabledDeptOptions.value = filterDisabledDept(
      JSON.parse(JSON.stringify(response.data))
    );
  });
}
/** 过滤禁用的部门 */
function filterDisabledDept(deptList) {
  return deptList.filter((dept) => {
    if (dept.disabled) {
      return false;
    }
    if (dept.children && dept.children.length) {
      dept.children = filterDisabledDept(dept.children);
    }
    return true;
  });
}
/** 节点单击事件 */
function handleNodeClick(data) {
  queryParams.value.deptId = data.id;
  mobileDeptOpen.value = false;
  handleQuery();
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
  queryParams.value.deptId = undefined;
  deptTreeRef.value?.setCurrentKey(null);
  handleQuery();
}
/** 删除按钮操作 */
function handleDelete(row) {
  const userIds = row.userId || ids.value;
  feedback
    .confirm('是否确认删除用户编号为"' + userIds + '"的数据项？')
    .then(function () {
      return delUser(userIds);
    })
    .then(() => {
      getList();
      feedback.msgSuccess("删除成功");
    })
    .catch(() => {});
}
/** 导出按钮操作 */
function handleExport() {
  proxy.download(
    "system/user/export",
    {
      ...queryParams.value,
    },
    `user_${new Date().getTime()}.xlsx`
  );
}
/** 用户状态修改  */
function handleStatusChange(row) {
  let text = row.status === "0" ? "启用" : "停用";
  feedback
    .confirm('确认要"' + text + '""' + row.userName + '"用户吗?')
    .then(function () {
      return changeUserStatus(row.userId, row.status);
    })
    .then(() => {
      feedback.msgSuccess(text + "成功");
    })
    .catch(function () {
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
    case "handleResetPwd":
      handleResetPwd(row);
      break;
    case "handleAuthRole":
      handleAuthRole(row);
      break;
    default:
      break;
  }
}
function userRowMenuItems(row) {
  if (row.userId === 1) return [];
  return [
    checkPermi(["system:user:edit"]) && { command: "handleUpdate", label: "修改", icon: EditOutlined },
    checkPermi(["system:user:remove"]) && { command: "handleDelete", label: "删除", icon: DeleteOutlined, danger: true },
    checkPermi(["system:user:resetPwd"]) && { command: "handleResetPwd", label: "重置密码", icon: KeyOutlined },
    checkPermi(["system:user:edit"]) && { command: "handleAuthRole", label: "分配角色", icon: SafetyCertificateOutlined },
  ].filter(Boolean);
}
/** 跳转角色分配 */
function handleAuthRole(row) {
  const userId = row.userId;
  router.push("/system/user-auth/role/" + userId);
}
/** 重置密码按钮操作 */
function handleResetPwd(row) {
  resetPasswordUser.value = row;
  resetPasswordForm.value = "";
  resetPasswordOpen.value = true;
  nextTick(() => resetPasswordRef.value?.clearValidate());
}
async function submitResetPassword() {
  if (!await resetPasswordRef.value.validateFields().then(() => true).catch(() => false)) return;
  const value = resetPasswordForm.value;
  await resetUserPwd(resetPasswordUser.value.userId, value);
  feedback.msgSuccess("修改成功，新密码是：" + value);
  resetPasswordOpen.value = false;
}
function clearResetPassword() {
  resetPasswordForm.value = "";
  resetPasswordUser.value = null;
  resetPasswordRef.value?.clearValidate();
}
/** 选择条数  */
function handleSelectionChange(selection) {
  ids.value = selection.map((item) => item.userId);
  single.value = selection.length != 1;
  multiple.value = !selection.length;
}
function toggleUserSelection(row) {
  const index = ids.value.indexOf(row.userId);
  if (index >= 0) ids.value.splice(index, 1);
  else ids.value.push(row.userId);
  single.value = ids.value.length != 1;
  multiple.value = !ids.value.length;
}
function toggleAll(event) {
  handleSelectionChange(event.target.checked ? userList.value : []);
}
/** 详情按钮操作 */
function handleViewData(row) {
  userViewRef.value.open(row.userId);
}
/** 导入按钮操作 */
function handleImport() {
  importUserRef.value.open();
}
/** 重置操作表单 */
function reset() {
  form.value = {
    userId: undefined,
    deptId: undefined,
    userName: undefined,
    nickName: undefined,
    password: undefined,
    phonenumber: undefined,
    email: undefined,
    sex: undefined,
    status: "0",
    remark: undefined,
    postIds: [],
    roleIds: [],
  };
  userRef.value?.clearValidate();
}
/** 取消按钮 */
function cancel() {
  open.value = false;
  reset();
}
/** 新增按钮操作 */
function handleAdd() {
  reset();
  getUser().then((response) => {
    postOptions.value = response.posts;
    roleOptions.value = response.roles;
    open.value = true;
    title.value = "添加用户";
    form.value.password = initPassword.value;
  });
}
/** 修改按钮操作 */
function handleUpdate(row) {
  reset();
  const userId = row.userId || ids.value;
  getUser(userId).then((response) => {
    form.value = response.data;
    postOptions.value = response.posts;
    roleOptions.value = response.roles;
    form.value.postIds = response.postIds;
    form.value.roleIds = response.roleIds;
    open.value = true;
    title.value = "修改用户";
    form.value.password = undefined;
  });
}
/** 提交按钮 */
function submitForm() {
  userRef.value.validateFields().then(() => {
      if (form.value.userId != undefined) {
        updateUser(form.value).then(() => {
          feedback.msgSuccess("修改成功");
          open.value = false;
          getList();
        });
      } else {
        addUser(form.value).then(() => {
          feedback.msgSuccess("新增成功");
          open.value = false;
          getList();
        });
      }
  }).catch(() => {});
}

onMounted(() => {
  getDeptTree();
  getList();
  proxy.getConfigKey("sys.user.initPassword").then((response) => {
    initPassword.value = response.msg;
  });
});
</script>

<style scoped>
.query-form { margin-bottom: 16px; row-gap: 8px; }
.table-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.toolbar-spacer { flex: 1 1 auto; }
.content-inner :deep(.ant-pagination) { margin-top: 16px; }
.mobile-row-actions { display: none; }
.mobile-dept-toolbar { margin-bottom: 12px; }
@media (max-width: 767px) {
  .tree-sidebar-manage-wrap { display: block; min-height: 0; overflow: visible; }
  .tree-sidebar-content, .tree-sidebar-content .content-inner { width: 100%; overflow: visible; }
  .tree-sidebar-content .content-inner { padding: 12px; }
  .mobile-dept-tree { width: 100% !important; min-height: calc(100vh - 180px); }
  .desktop-row-actions { display: none; }
  .mobile-row-actions { display: inline-flex; }
}
</style>
