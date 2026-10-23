<template>
  <ResourceDrawer :open="visible" title="文件保留提醒" width="88%" @close="visible = false">
    <a-form ref="queryRef" :model="queryParams" layout="inline" class="query-form">
      <a-form-item label="文件名称" name="originalName"><a-input v-model:value="queryParams.originalName" allow-clear placeholder="请输入文件名称" @press-enter="handleQuery" /></a-form-item>
      <a-form-item label="提醒类型" name="noticeType"><a-select v-model:value="queryParams.noticeType" allow-clear placeholder="请选择提醒类型" :options="noticeTypeOptions" /></a-form-item>
      <a-form-item label="提醒状态" name="status"><a-select v-model:value="queryParams.status" allow-clear placeholder="请选择提醒状态" :options="statusOptions" /></a-form-item>
      <a-form-item><a-space><a-button type="primary" @click="handleQuery"><SearchOutlined />搜索</a-button><a-button @click="resetQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
    </a-form>
    <a-space class="mb8"><a-button type="primary" :loading="scanning" @click="handleScan" v-hasPermi="['system:file:edit']"><ReloadOutlined />扫描</a-button><a-button :disabled="!ids.length" @click="handleRead"><CheckOutlined />已读</a-button></a-space>
    <ResourceTable :columns="columns" :rows="reminderList" :loading="loading" :pagination="false" row-key="noticeId" :scroll="{ x: 1000 }">
      <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
      <template #bodyCell="{ column, record: row }">
        <a-checkbox v-if="column.key === 'selection'" :disabled="row.status !== '0'" :checked="ids.includes(row.noticeId)" @change="event => toggleRow(row, event.target.checked)" />
        <template v-else-if="column.key === 'ownerName'">{{ row.ownerName || '-' }}</template><template v-else-if="column.key === 'deptName'">{{ row.deptName || '-' }}</template>
        <a-tag v-else-if="column.key === 'noticeType'" :color="row.noticeType === 'expired' ? 'red' : 'orange'">{{ row.noticeType === 'expired' ? '已到期' : '即将到期' }}</a-tag>
        <template v-else-if="column.key === 'expireTime'">{{ parseTime(row.expireTime) }}</template><a-tag v-else-if="column.key === 'status'" :color="row.status === '0' ? 'orange' : 'green'">{{ row.status === '0' ? '未读' : '已读' }}</a-tag>
        <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template><template v-else-if="column.key === 'readBy'">{{ row.readBy || '-' }}</template>
        <a-space v-else-if="column.key === 'actions'"><a-tooltip title="延长保留期限"><a-button type="link" @click="handleExtend(row)" v-hasPermi="['system:file:edit']"><ClockCircleOutlined /></a-button></a-tooltip><a-tooltip v-if="isExpired(row)" :title="row.canDispose ? '移入回收站' : '存在永久或尚未到期的业务引用，暂不可处置'"><a-button type="link" danger :disabled="!row.canDispose" @click="handleDispose(row)" v-hasPermi="['system:file:remove']"><DeleteOutlined /></a-button></a-tooltip></a-space>
      </template>
    </ResourceTable>
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" show-size-changer @change="getList" />
  </ResourceDrawer>

  <a-modal v-model:open="extendOpen" title="延长文件保留期限" :width="520">
    <a-form ref="extendRef" :model="extendForm" :rules="extendRules" layout="vertical"><a-form-item label="文件名称">{{ currentRow.originalName }}</a-form-item><a-form-item label="原到期时间">{{ parseTime(currentRow.expireTime) }}</a-form-item><a-form-item label="新到期时间" name="expireTime"><a-date-picker v-model:value="extendForm.expireTime" value-format="YYYY-MM-DD HH:mm:ss" show-time style="width:100%" /></a-form-item><a-form-item label="延期原因" name="reason"><a-textarea v-model:value="extendForm.reason" :rows="3" :maxlength="500" show-count /></a-form-item></a-form>
    <template #footer><a-button @click="extendOpen = false">取消</a-button><a-button type="primary" :loading="submitting" @click="submitExtend">确定</a-button></template>
  </a-modal>
  <a-modal v-model:open="disposeOpen" title="到期文件处置" :width="520" :mask-closable="false">
    <a-alert :message="`处置后“${disposeRow.originalName || ''}”将移入回收站，并解除${disposeRow.referenceCount || 0}条已到期业务引用。`" type="warning" show-icon class="mb8" />
    <a-form ref="disposeRef" :model="disposeForm" :rules="disposeRules" layout="vertical"><a-form-item label="处置原因" name="reason"><a-textarea v-model:value="disposeForm.reason" :maxlength="500" show-count /></a-form-item></a-form>
    <template #footer><a-button @click="disposeOpen = false">取消</a-button><a-button type="primary" danger @click="submitDispose">确定处置</a-button></template>
  </a-modal>
</template>

<script setup>
import { CheckOutlined, ClockCircleOutlined, DeleteOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import feedback from '@/plugins/antdFeedback'
import {
  disposeExpiredFile,
  extendFileRetention,
  listFileRetentionReminder,
  readFileRetentionReminder,
  scanFileRetentionReminder
} from "@/api/system/file";

const emit = defineEmits(["refresh"]);
const { proxy } = getCurrentInstance();
const visible = ref(false);
const loading = ref(false);
const scanning = ref(false);
const reminderList = ref([]);
const total = ref(0);
const ids = ref([]);
const queryRef = ref();
const extendRef = ref();
const extendOpen = ref(false);
const submitting = ref(false);
const currentRow = ref({});
const disposeOpen = ref(false);
const disposeRef = ref();
const disposeRow = ref({});
const disposeForm = reactive({ reason: "" });
const extendForm = reactive({
  expireTime: undefined,
  reason: undefined
});
const extendRules = {
  expireTime: [
    { required: true, message: "新的到期时间不能为空", trigger: "change" }
  ],
  reason: [
    { required: true, message: "延期原因不能为空", trigger: "blur" }
  ]
};
const queryParams = reactive({
  pageNum: 1,
  pageSize: 10,
  originalName: undefined,
  noticeType: undefined,
  status: "0"
});
const noticeTypeOptions = [{label:'即将到期',value:'expiring'},{label:'已到期',value:'expired'}];
const statusOptions = [{label:'未读',value:'0'},{label:'已读',value:'1'}];
const columns = [{title:'',key:'selection',width:48},{title:'文件名称',dataIndex:'originalName',key:'originalName',ellipsis:true},{title:'所有者',key:'ownerName',width:120},{title:'所属部门',key:'deptName',width:140},{title:'提醒类型',key:'noticeType',width:110},{title:'到期时间',key:'expireTime',width:180},{title:'状态',key:'status',width:90},{title:'业务引用',dataIndex:'referenceCount',key:'referenceCount',width:90},{title:'提醒时间',key:'createTime',width:180},{title:'读取人',key:'readBy',width:110},{title:'操作',key:'actions',width:100}];
const selectableRows = computed(() => reminderList.value.filter(row => row.status === '0'));
const allSelected = computed(() => selectableRows.value.length > 0 && selectableRows.value.every(row => ids.value.includes(row.noticeId)));
const someSelected = computed(() => ids.value.length > 0 && !allSelected.value);
const disposeRules = { reason: [{ required:true, message:'处置原因不能为空', trigger:'blur' }, { max:500, message:'处置原因不能超过500个字符', trigger:'blur' }] };
function toggleRow(row, checked) { ids.value = checked ? [...new Set([...ids.value,row.noticeId])] : ids.value.filter(id => id !== row.noticeId); }
function toggleAll(event) { ids.value = event.target.checked ? selectableRows.value.map(row => row.noticeId) : []; }

function open() {
  visible.value = true;
  queryParams.pageNum = 1;
  getList();
}

function getList() {
  loading.value = true;
  listFileRetentionReminder(queryParams)
    .then(response => {
      reminderList.value = response.rows;
      total.value = response.total;
      ids.value = [];
    })
    .finally(() => {
      loading.value = false;
    });
}

function handleQuery() {
  queryParams.pageNum = 1;
  getList();
}

function resetQuery() {
  queryRef.value?.resetFields();
  queryParams.pageNum = 1;
  getList();
}

function handleScan() {
  scanning.value = true;
  scanFileRetentionReminder()
    .then(response => {
      const { expiringCount, expiredCount } = response.data;
      feedback.msgSuccess(
        `扫描完成，新增即将到期${expiringCount}条、已到期${expiredCount}条提醒`
      );
      getList();
      emit("refresh");
    })
    .finally(() => {
      scanning.value = false;
    });
}

function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.noticeId);
}

function handleRead() {
  readFileRetentionReminder(ids.value.join(",")).then(() => {
    feedback.msgSuccess("提醒已标记为已读");
    getList();
  });
}

function isExpired(row) {
  return new Date(row.expireTime).getTime() <= Date.now();
}

function handleExtend(row) {
  currentRow.value = row;
  Object.assign(extendForm, {
    expireTime: defaultExtendTime(row.expireTime),
    reason: undefined
  });
  extendOpen.value = true;
  nextTick(() => extendRef.value?.clearValidate());
}

function submitExtend() {
  extendRef.value.validateFields().then(() => {
    submitting.value = true;
    extendFileRetention(currentRow.value.noticeId, extendForm)
      .then(() => {
        feedback.msgSuccess("文件保留期限已延长");
        extendOpen.value = false;
        getList();
        emit("refresh");
      })
      .finally(() => {
        submitting.value = false;
      });
  }).catch(() => {});
}

function handleDispose(row) {
  disposeRow.value = row;
  disposeForm.reason = "";
  disposeOpen.value = true;
  nextTick(() => disposeRef.value?.clearValidate());
}
async function submitDispose() {
  if (!await disposeRef.value.validateFields().then(() => true).catch(() => false)) return;
  await disposeExpiredFile(disposeRow.value.noticeId, { reason: disposeForm.reason.trim() });
  feedback.msgSuccess("到期文件已移入回收站");
  disposeOpen.value = false;
  getList(); emit("refresh");
}

function defaultExtendTime(expireTime) {
  const baseTime = Math.max(Date.now(), new Date(expireTime).getTime());
  const target = new Date(baseTime + 30 * 24 * 60 * 60 * 1000);
  const pad = value => String(value).padStart(2, "0");
  return `${target.getFullYear()}-${pad(target.getMonth() + 1)}-${pad(
    target.getDate()
  )} ${pad(target.getHours())}:${pad(target.getMinutes())}:${pad(
    target.getSeconds()
  )}`;
}

defineExpose({ open });
</script>

<style scoped>
.query-form { margin-bottom:16px; row-gap:8px; }
:deep(.ant-pagination) { margin-top:16px; }
</style>
