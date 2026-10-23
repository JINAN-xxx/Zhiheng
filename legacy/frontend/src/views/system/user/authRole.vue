<template>
   <WorkspacePage title="分配角色" subtitle="配置用户拥有的系统角色">
      <h4 class="form-header h4">基本信息</h4>
      <a-form :model="form" layout="vertical">
         <a-row :gutter="20">
            <a-col :xs="24" :md="12">
               <a-form-item label="用户昵称" name="nickName">
                  <a-input v-model:value="form.nickName" disabled />
               </a-form-item>
            </a-col>
            <a-col :xs="24" :md="12">
               <a-form-item label="登录账号" name="userName">
                  <a-input v-model:value="form.userName" disabled />
               </a-form-item>
            </a-col>
         </a-row>
      </a-form>

      <h4 class="form-header h4">角色信息</h4>
      <a-table
         :columns="roleColumns"
         :data-source="pagedRoles"
         :loading="loading"
         :pagination="false"
         :row-key="getRowKey"
         :row-selection="rowSelection"
         :custom-row="(row) => ({ onClick: () => clickRow(row) })"
         :scroll="{ x: 720 }"
      >
         <template #bodyCell="{ column, record: row, index }">
            <template v-if="column.key === 'index'">{{ (pageNum - 1) * pageSize + index + 1 }}</template>
            <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template>
         </template>
      </a-table>

      <a-pagination v-if="total > 0" v-model:current="pageNum" v-model:page-size="pageSize" :total="total" show-size-changer />

      <a-form>
         <div class="form-actions">
            <a-button type="primary" @click="submitForm()"><SaveOutlined />提交</a-button>
            <a-button @click="close()"><ArrowLeftOutlined />返回</a-button>
         </div>
      </a-form>
   </WorkspacePage>
</template>

<script setup name="AuthRole">
import { ArrowLeftOutlined, SaveOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import { getAuthRole, updateAuthRole } from "@/api/system/user";

const route = useRoute();
const { proxy } = getCurrentInstance();

const loading = ref(true);
const total = ref(0);
const pageNum = ref(1);
const pageSize = ref(10);
const roleIds = ref([]);
const roles = ref([]);
const pagedRoles = computed(() => roles.value.slice((pageNum.value - 1) * pageSize.value, pageNum.value * pageSize.value));
const roleColumns = [
  { title: "序号", key: "index", width: 70 },
  { title: "角色编号", dataIndex: "roleId", key: "roleId" },
  { title: "角色名称", dataIndex: "roleName", key: "roleName" },
  { title: "权限字符", dataIndex: "roleKey", key: "roleKey" },
  { title: "创建时间", dataIndex: "createTime", key: "createTime", width: 180 },
];
const rowSelection = computed(() => ({
  selectedRowKeys: roleIds.value,
  preserveSelectedRowKeys: true,
  getCheckboxProps: (row) => ({ disabled: !checkSelectable(row) }),
  onChange: (keys) => handleSelectionChange(keys),
}));
const form = ref({
  nickName: undefined,
  userName: undefined,
  userId: undefined
});

/** 单击选中行数据 */
function clickRow(row) {
  if (checkSelectable(row)) {
    const index = roleIds.value.indexOf(row.roleId);
    if (index >= 0) roleIds.value.splice(index, 1);
    else roleIds.value.push(row.roleId);
  }
};

/** 多选框选中数据 */
function handleSelectionChange(keys) {
  roleIds.value = keys;
};

/** 保存选中的数据编号 */
function getRowKey(row) {
  return row.roleId;
};

// 检查角色状态
function checkSelectable(row) {
  return row.status === "0" ? true : false;
};

/** 关闭按钮 */
function close() {
  const obj = { path: "/system/user" };
  proxy.$tab.closeOpenPage(obj);
};

/** 提交按钮 */
function submitForm() {
  const userId = form.value.userId;
  const rIds = roleIds.value.join(",");
  updateAuthRole({ userId: userId, roleIds: rIds }).then(() => {
    feedback.msgSuccess("授权成功");
    close();
  });
};

(() => {
  const userId = route.params && route.params.userId;
  if (userId) {
    loading.value = true;
    getAuthRole(userId).then(response => {
      form.value = response.user;
      roles.value = response.roles;
      total.value = roles.value.length;
      roleIds.value = roles.value.filter(row => row.flag).map(row => row.roleId);
      loading.value = false;
    });
  }
})();
</script>

<style scoped>
.form-actions { display: flex; justify-content: center; gap: 8px; margin-top: 30px; }
</style>
