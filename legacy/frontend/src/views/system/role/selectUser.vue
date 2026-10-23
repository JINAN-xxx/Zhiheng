<template>
   <!-- 授权用户 -->
   <a-modal title="选择用户" v-model:open="visible" :width="800" :style="{ top: '5vh' }">
      <a-form :model="queryParams" ref="queryRef" layout="inline" class="query-form">
         <a-form-item label="用户名称" name="userName">
            <a-input
               v-model:value="queryParams.userName"
               placeholder="请输入用户名称"
               allow-clear
               style="width: 180px"
               @press-enter="handleQuery"
            />
         </a-form-item>
         <a-form-item label="手机号码" name="phonenumber">
            <a-input
               v-model:value="queryParams.phonenumber"
               placeholder="请输入手机号码"
               allow-clear
               style="width: 180px"
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
      <ResourceTable :columns="userColumns" :rows="userList" :pagination="false" row-key="userId" :scroll="{ y: 260, x: 720 }">
         <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
         <template #bodyCell="{ column, record: row }">
            <template v-if="column.key === 'selection'">
               <a-checkbox :checked="userIds.includes(row.userId)" @change="() => clickRow(row)" />
            </template>
            <template v-else-if="column.key === 'status'"><dict-tag :options="sys_normal_disable" :value="row.status" /></template>
            <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template>
         </template>
      </ResourceTable>
      <a-pagination
         v-if="total > 0"
         v-model:current="queryParams.pageNum"
         v-model:page-size="queryParams.pageSize"
         :total="total"
         show-size-changer
         @change="getList"
      />
      <template #footer>
         <a-button @click="visible = false">取消</a-button>
         <a-button type="primary" @click="handleSelectUser">确定</a-button>
      </template>
   </a-modal>
</template>

<script setup name="SelectUser">
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import feedback from '@/plugins/antdFeedback'
import { authUserSelectAll, unallocatedUserList } from "@/api/system/role";

const props = defineProps({
  roleId: {
    type: [Number, String]
  }
});

const { proxy } = getCurrentInstance();
const { sys_normal_disable } = proxy.useDict("sys_normal_disable");

const userList = ref([]);
const visible = ref(false);
const total = ref(0);
const userIds = ref([]);
const queryRef = ref();
const rowClickCell = (row) => ({ onClick: () => clickRow(row) });
const userColumns = [
  { title: "", key: "selection", width: 48 },
  { title: "用户名称", dataIndex: "userName", key: "userName", ellipsis: true, customCell: rowClickCell },
  { title: "用户昵称", dataIndex: "nickName", key: "nickName", ellipsis: true, customCell: rowClickCell },
  { title: "邮箱", dataIndex: "email", key: "email", ellipsis: true, customCell: rowClickCell, mobileHidden: true },
  { title: "手机", dataIndex: "phonenumber", key: "phonenumber", ellipsis: true, customCell: rowClickCell },
  { title: "状态", dataIndex: "status", key: "status", width: 90, customCell: rowClickCell },
  { title: "创建时间", dataIndex: "createTime", key: "createTime", width: 180, customCell: rowClickCell, mobileHidden: true },
];
const allSelected = computed(() => userList.value.length > 0 && userList.value.every((item) => userIds.value.includes(item.userId)));
const someSelected = computed(() => userIds.value.length > 0 && !allSelected.value);

const queryParams = reactive({
  pageNum: 1,
  pageSize: 10,
  roleId: undefined,
  userName: undefined,
  phonenumber: undefined
});

// 显示弹框
function show() {
  queryParams.roleId = props.roleId;
  getList();
  visible.value = true;
}
/**选择行 */
function clickRow(row) {
  const index = userIds.value.indexOf(row.userId);
  if (index >= 0) userIds.value.splice(index, 1);
  else userIds.value.push(row.userId);
}
function toggleAll(event) {
  userIds.value = event.target.checked ? userList.value.map((item) => item.userId) : [];
}
// 查询表数据
function getList() {
  unallocatedUserList(queryParams).then(res => {
    userList.value = res.rows;
    total.value = res.total;
    userIds.value = [];
  });
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
const emit = defineEmits(["ok"]);
/** 选择授权用户操作 */
function handleSelectUser() {
  const roleId = queryParams.roleId;
  const uIds = userIds.value.join(",");
  if (uIds == "") {
    feedback.msgError("请选择要分配的用户");
    return;
  }
  authUserSelectAll({ roleId: roleId, userIds: uIds }).then(res => {
    feedback.msgSuccess(res.msg);
    visible.value = false;
    emit("ok");
  });
}

defineExpose({
  show,
});
</script>

<style scoped>
.query-form { margin-bottom: 16px; row-gap: 8px; }
</style>
