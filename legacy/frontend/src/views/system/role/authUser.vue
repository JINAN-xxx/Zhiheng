
<template>
   <WorkspacePage title="分配用户" subtitle="管理当前角色的用户授权">
      <a-form :model="queryParams" ref="queryRef" v-show="showSearch" layout="inline" class="query-form">
         <a-form-item label="用户名称" name="userName">
            <a-input
               v-model:value="queryParams.userName"
               placeholder="请输入用户名称"
               allow-clear
               style="width: 240px"
               @press-enter="handleQuery"
            />
         </a-form-item>
         <a-form-item label="手机号码" name="phonenumber">
            <a-input
               v-model:value="queryParams.phonenumber"
               placeholder="请输入手机号码"
               allow-clear
               style="width: 240px"
               @press-enter="handleQuery"
            />
         </a-form-item>
         <a-form-item>
            <a-space>
               <a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button>
               <a-button @click="resetQuery"><ReloadOutlined />重置</a-button>
            </a-space>
         </a-form-item>
      </a-form>

      <div class="table-toolbar mb8">
            <a-button
               type="primary"
               @click="openSelectUser"
               v-hasPermi="['system:role:add']"
            ><PlusOutlined />添加用户</a-button>
            <a-button
               danger
               :disabled="multiple"
               @click="cancelAuthUserAll"
               v-hasPermi="['system:role:remove']"
            ><StopOutlined />批量取消授权</a-button>
            <a-button
               @click="handleClose"
            ><CloseOutlined />关闭</a-button>
            <span class="toolbar-spacer" />
            <a-tooltip title="显示或隐藏筛选"><a-button aria-label="显示或隐藏筛选" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip>
            <a-tooltip title="刷新"><a-button aria-label="刷新" :loading="loading" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
      </div>

      <ResourceTable :columns="userColumns" :rows="userList" :loading="loading" :pagination="false" row-key="userId" :scroll="{ x: 820 }">
         <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
         <template #bodyCell="{ column, record: row }">
            <template v-if="column.key === 'selection'"><a-checkbox :checked="userIds.includes(row.userId)" @change="() => toggleSelection(row)" /></template>
            <template v-else-if="column.key === 'status'"><dict-tag :options="sys_normal_disable" :value="row.status" /></template>
            <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template>
            <template v-else-if="column.key === 'actions'">
               <a-button type="link" danger @click="cancelAuthUser(row)" v-hasPermi="['system:role:remove']"><StopOutlined />取消授权</a-button>
            </template>
         </template>
      </ResourceTable>

      <a-pagination
         v-if="total > 0"
         :total="total"
         v-model:current="queryParams.pageNum"
         v-model:page-size="queryParams.pageSize"
         show-size-changer
         @change="getList"
      />
      <select-user ref="selectRef" :roleId="queryParams.roleId" @ok="handleQuery" />
   </WorkspacePage>
</template>

<script setup name="AuthUser">
import { CloseOutlined, FilterOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, StopOutlined } from '@ant-design/icons-vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import selectUser from "./selectUser";
import { allocatedUserList, authUserCancel, authUserCancelAll } from "@/api/system/role";

const route = useRoute();
const { proxy } = getCurrentInstance();
const { sys_normal_disable } = proxy.useDict("sys_normal_disable");

const userList = ref([]);
const loading = ref(true);
const showSearch = ref(true);
const multiple = ref(true);
const total = ref(0);
const userIds = ref([]);
const queryRef = ref();
const selectRef = ref();
const userColumns = [
  { title: "", key: "selection", width: 48 },
  { title: "用户名称", dataIndex: "userName", key: "userName", ellipsis: true },
  { title: "用户昵称", dataIndex: "nickName", key: "nickName", ellipsis: true },
  { title: "邮箱", dataIndex: "email", key: "email", ellipsis: true, mobileHidden: true },
  { title: "手机", dataIndex: "phonenumber", key: "phonenumber", ellipsis: true },
  { title: "状态", dataIndex: "status", key: "status", width: 90 },
  { title: "创建时间", dataIndex: "createTime", key: "createTime", width: 180, mobileHidden: true },
  { title: "操作", key: "actions", width: 130 },
];
const allSelected = computed(() => userList.value.length > 0 && userList.value.every((item) => userIds.value.includes(item.userId)));
const someSelected = computed(() => userIds.value.length > 0 && !allSelected.value);

const queryParams = reactive({
  pageNum: 1,
  pageSize: 10,
  roleId: route.params.roleId,
  userName: undefined,
  phonenumber: undefined,
});

/** 查询授权用户列表 */
function getList() {
  loading.value = true;
  allocatedUserList(queryParams).then(response => {
    userList.value = response.rows;
    total.value = response.total;
    handleSelectionChange([]);
    loading.value = false;
  });
}
// 返回按钮
function handleClose() {
  const obj = { path: "/system/role" };
  proxy.$tab.closeOpenPage(obj);
}
/** 搜索按钮操作 */
function handleQuery() {
  queryParams.pageNum = 1;
  getList();
}
/** 重置按钮操作 */
function resetQuery() {
  queryRef.value?.resetFields();
  handleQuery();
}
// 多选框选中数据
function handleSelectionChange(selection) {
  userIds.value = selection.map(item => item.userId);
  multiple.value = !selection.length;
}
function toggleSelection(row) {
  const index = userIds.value.indexOf(row.userId);
  if (index >= 0) userIds.value.splice(index, 1);
  else userIds.value.push(row.userId);
  multiple.value = !userIds.value.length;
}
function toggleAll(event) {
  handleSelectionChange(event.target.checked ? userList.value : []);
}
/** 打开授权用户表弹窗 */
function openSelectUser() {
  selectRef.value.show();
}
/** 取消授权按钮操作 */
function cancelAuthUser(row) {
  feedback.confirm('确认要取消该用户"' + row.userName + '"角色吗？').then(function () {
    return authUserCancel({ userId: row.userId, roleId: queryParams.roleId });
  }).then(() => {
    getList();
    feedback.msgSuccess("取消授权成功");
  }).catch(() => {});
}
/** 批量取消授权按钮操作 */
function cancelAuthUserAll() {
  const roleId = queryParams.roleId;
  const uIds = userIds.value.join(",");
  feedback.confirm("是否取消选中用户授权数据项?").then(function () {
    return authUserCancelAll({ roleId: roleId, userIds: uIds });
  }).then(() => {
    getList();
    feedback.msgSuccess("取消授权成功");
  }).catch(() => {});
}

getList();
</script>

<style scoped>
.query-form { margin-bottom: 16px; row-gap: 8px; }
.table-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.toolbar-spacer { flex: 1 1 auto; }
</style>
