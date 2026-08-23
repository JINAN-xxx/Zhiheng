<template>
  <ResourceDrawer :open="visible" title="文件存储对账" width="88%" @close="closeDrawer">
    <a-alert message="对账会双向检查文件信息表与公开、受保护、回收站存储区；隔离区不对外提供静态访问。修复操作仅使用异常中已校验的相对路径。" type="info" show-icon class="mb12" />
    <div class="reconcile-summary">
      <div class="summary-card summary-card--danger"><div class="summary-card__label">待处理</div><div class="summary-card__value">{{ stats.openCount }}</div><div class="summary-card__hint">需要确认或修复的异常</div></div>
      <div class="summary-card summary-card--critical"><div class="summary-card__label">严重异常</div><div class="summary-card__value">{{ stats.criticalCount }}</div><div class="summary-card__hint">文件缺失、错位或内容不一致</div></div>
      <div class="summary-card summary-card--warning"><div class="summary-card__label">警告异常</div><div class="summary-card__value">{{ stats.warningCount }}</div><div class="summary-card__hint">孤立文件或回收状态异常</div></div>
      <div class="summary-card summary-card--quarantine"><div class="summary-card__label">隔离文件</div><div class="summary-card__value">{{ stats.quarantinedCount }}</div><div class="summary-card__hint">仅管理员可恢复或永久删除</div></div>
      <div class="summary-card summary-card--run"><div class="summary-card__label">最近任务</div><div class="summary-card__status"><a-tag :color="tagColor(runStatusType(stats.latestRun?.status))">{{ runStatusLabel(stats.latestRun?.status) }}</a-tag></div><div class="summary-card__hint">{{ stats.latestRun ? parseTime(stats.latestRun.startedTime) : '尚未执行' }}</div></div>
    </div>
    <div class="reconcile-toolbar"><div class="reconcile-toolbar__tip">完整摘要校验会读取全部文件内容，建议在低峰期启用。</div><div class="reconcile-toolbar__actions"><span class="mr8">校验 SHA-256</span><a-switch v-model:checked="checkHash" class="mr12" /><a-button type="primary" :loading="scanning" :disabled="stats.latestRun?.status === 'running'" @click="handleStart"><ReloadOutlined />开始对账</a-button></div></div>
    <a-tabs v-model:active-key="activeTab" @change="handleTabChange">
      <a-tab-pane key="issues" tab="异常明细">
        <a-form ref="issueQueryRef" :model="issueQuery" layout="inline" class="query-form">
          <a-form-item label="关键字" name="keyword"><a-input v-model:value="issueQuery.keyword" allow-clear placeholder="文件名、ID或相对路径" @press-enter="handleIssueQuery" /></a-form-item>
          <a-form-item label="异常类型" name="issueType"><a-select v-model:value="issueQuery.issueType" allow-clear placeholder="全部类型" :options="issueTypeOptions" /></a-form-item>
          <a-form-item label="严重级别" name="severity"><a-select v-model:value="issueQuery.severity" allow-clear placeholder="全部级别" :options="severityOptions" /></a-form-item>
          <a-form-item label="处理状态" name="status"><a-select v-model:value="issueQuery.status" allow-clear placeholder="全部状态" :options="issueStatusOptions" /></a-form-item>
          <a-form-item><a-space><a-button type="primary" @click="handleIssueQuery"><SearchOutlined />搜索</a-button><a-button @click="resetIssueQuery"><ReloadOutlined />重置</a-button></a-space></a-form-item>
        </a-form>
        <ResourceTable :columns="issueColumns" :rows="issueList" :loading="issueLoading" :pagination="false" row-key="issueId" :scroll="{ x: 1250 }">
          <template #bodyCell="{ column, record: row }">
            <a-tag v-if="column.key === 'severity'" :color="tagColor(severityTagType(row.severity))">{{ severityLabel(row.severity) }}</a-tag>
            <template v-else-if="column.key === 'issueType'">{{ issueTypeLabel(row.issueType) }}</template>
            <template v-else-if="column.key === 'file'"><div>{{ row.originalName || '未登记文件' }}</div><div class="cell-secondary">{{ row.fileId || '-' }}</div></template>
            <template v-else-if="column.key === 'location'"><div>预期：{{ formatLocation(row.expectedRoot,row.expectedKey) }}</div><div class="cell-secondary">实际：{{ formatLocation(row.actualRoot,row.actualKey) }}</div></template>
            <template v-else-if="column.key === 'size'"><div>预期：{{ formatOptionalSize(row.expectedSize) }}</div><div class="cell-secondary">实际：{{ formatOptionalSize(row.actualSize) }}</div></template>
            <a-tag v-else-if="column.key === 'status'" :color="tagColor(issueStatusType(row.status))">{{ issueStatusLabel(row.status) }}</a-tag>
            <template v-else-if="column.key === 'lastSeen'">{{ parseTime(row.lastSeenTime) }}</template>
            <template v-else-if="column.key === 'actions'"><a-dropdown v-if="row.availableActions?.length" :disabled="stats.latestRun?.status === 'running'" :trigger="['click']"><a-button type="link" :disabled="stats.latestRun?.status === 'running'"><SettingOutlined /></a-button><template #overlay><a-menu @click="({key}) => handleCommand(key,row)"><a-menu-item v-for="action in row.availableActions" :key="action">{{ actionLabel(action) }}</a-menu-item></a-menu></template></a-dropdown><span v-else>-</span></template>
          </template>
        </ResourceTable>
        <a-pagination v-if="issueTotal > 0" v-model:current="issueQuery.pageNum" v-model:page-size="issueQuery.pageSize" :total="issueTotal" show-size-changer @change="getIssueList" />
      </a-tab-pane>
      <a-tab-pane key="runs" tab="任务记录">
        <ResourceTable :columns="runColumns" :rows="runList" :loading="runLoading" :pagination="false" row-key="runId" :scroll="{ x: 1150 }">
          <template #bodyCell="{ column, record: row }"><a-tag v-if="column.key === 'status'" :color="tagColor(runStatusType(row.status))">{{ runStatusLabel(row.status) }}</a-tag><template v-else-if="column.key === 'triggerType'">{{ row.triggerType === 'scheduled' ? '定时任务' : '手动' }}</template><template v-else-if="column.key === 'checkHash'">{{ row.checkHash ? '是' : '否' }}</template><template v-else-if="column.key === 'startedTime'">{{ parseTime(row.startedTime) }}</template><template v-else-if="column.key === 'finishedTime'">{{ row.finishedTime ? parseTime(row.finishedTime) : '-' }}</template></template>
        </ResourceTable>
        <a-pagination v-if="runTotal > 0" v-model:current="runQuery.pageNum" v-model:page-size="runQuery.pageSize" :total="runTotal" show-size-changer @change="getRunList" />
      </a-tab-pane>
    </a-tabs>
  </ResourceDrawer>
  <a-modal v-model:open="handleOpen" :title="actionLabel(handleForm.action)" :width="520">
    <a-alert v-if="actionDescriptions[handleForm.action]" :message="actionDescriptions[handleForm.action]" :type="dangerousActions.includes(handleForm.action) ? 'warning' : 'info'" show-icon class="mb16" />
    <a-form ref="handleFormRef" :model="handleForm" :rules="handleRules" layout="vertical"><a-form-item v-if="handleForm.action === 'register_orphan'" label="原始文件名" name="originalName"><a-input v-model:value="handleForm.originalName" :maxlength="255" placeholder="需与物理文件扩展名一致" /></a-form-item><a-form-item label="处理原因" name="reason"><a-textarea v-model:value="handleForm.reason" :rows="4" :maxlength="500" show-count placeholder="请填写处理依据，内容会保留在对账记录中" /></a-form-item></a-form>
    <template #footer><a-button @click="handleOpen = false">取消</a-button><a-button type="primary" :danger="dangerousActions.includes(handleForm.action)" :loading="handling" @click="submitHandle">确定</a-button></template>
  </a-modal>
</template>
<script setup>
import { ReloadOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import feedback from '@/plugins/antdFeedback'
import {
  getFileReconcileStats,
  handleFileReconcileIssue,
  listFileReconcileIssue,
  listFileReconcileRun,
  startFileReconcile
} from "@/api/system/file";
import { formatFileSize } from "./fileFormatters";

const emit = defineEmits(["refresh"]);
const { proxy } = getCurrentInstance();
const visible = ref(false);
const activeTab = ref("issues");
const issueLoading = ref(false);
const runLoading = ref(false);
const scanning = ref(false);
const handling = ref(false);
const checkHash = ref(false);
const issueList = ref([]);
const runList = ref([]);
const issueTotal = ref(0);
const runTotal = ref(0);
const issueQueryRef = ref();
const handleFormRef = ref();
const handleOpen = ref(false);
const currentIssueId = ref();
let pollTimer;

const stats = reactive({
  openCount: 0,
  criticalCount: 0,
  warningCount: 0,
  ignoredCount: 0,
  quarantinedCount: 0,
  latestRun: undefined
});
const issueQuery = reactive({
  pageNum: 1,
  pageSize: 10,
  keyword: undefined,
  issueType: undefined,
  severity: undefined,
  status: "open"
});
const runQuery = reactive({
  pageNum: 1,
  pageSize: 10
});
const handleForm = reactive({
  action: "",
  reason: "",
  originalName: ""
});
const handleRules = {
  reason: [{ required: true, message: "处理原因不能为空", trigger: "blur" }],
  originalName: [
    {
      validator: (_rule, value, callback) => {
        if (handleForm.action === "register_orphan" && !value) {
          callback(new Error("登记孤立文件时必须填写原始文件名"));
          return;
        }
        callback();
      },
      trigger: "blur"
    }
  ]
};
const issueTypeOptions = [
  { label: "元数据不合法", value: "invalid_metadata" },
  { label: "物理文件缺失", value: "missing_file" },
  { label: "有效文件位于回收站", value: "unexpected_trash" },
  { label: "回收文件位于正式区", value: "unexpected_source" },
  { label: "重复物理文件", value: "duplicate_file" },
  { label: "存储区域错误", value: "wrong_storage_root" },
  { label: "文件大小不一致", value: "size_mismatch" },
  { label: "文件摘要不一致", value: "hash_mismatch" },
  { label: "孤立物理文件", value: "orphan_file" },
  { label: "不安全目录项", value: "unsafe_entry" }
];
const severityOptions = [{label:'严重',value:'critical'},{label:'警告',value:'warning'},{label:'提示',value:'info'}];
const issueStatusOptions = [{label:'待处理',value:'open'},{label:'已忽略',value:'ignored'},{label:'已隔离',value:'quarantined'},{label:'已解决',value:'resolved'}];
const issueColumns = [{title:'级别',key:'severity',width:80},{title:'异常类型',key:'issueType',width:130},{title:'文件',key:'file',width:180},{title:'存储位置',key:'location',width:260},{title:'大小',key:'size',width:140},{title:'状态',key:'status',width:90},{title:'异常说明',dataIndex:'detail',key:'detail',width:180,ellipsis:true},{title:'最近发现',key:'lastSeen',width:170},{title:'操作',key:'actions',width:70}];
const runColumns = [{title:'任务状态',key:'status',width:100},{title:'触发方式',key:'triggerType',width:100},{title:'摘要校验',key:'checkHash',width:90},{title:'文件记录',dataIndex:'scannedFileCount',key:'scannedFileCount',width:100},{title:'物理文件',dataIndex:'scannedStorageCount',key:'scannedStorageCount',width:100},{title:'发现异常',dataIndex:'issueCount',key:'issueCount',width:100},{title:'新增异常',dataIndex:'newIssueCount',key:'newIssueCount',width:100},{title:'自动恢复',dataIndex:'resolvedIssueCount',key:'resolvedIssueCount',width:100},{title:'发起人',dataIndex:'startedBy',key:'startedBy',width:110,ellipsis:true},{title:'开始时间',key:'startedTime',width:170},{title:'完成时间',key:'finishedTime',width:170},{title:'失败原因',dataIndex:'errorMessage',key:'errorMessage',width:180,ellipsis:true}];
const tagColor = type => ({danger:'red',warning:'orange',success:'green',info:'default',primary:'blue'}[type] || type);
const dangerousActions = [
  "delete_quarantine",
  "accept_current",
  "register_orphan"
];
const actionDescriptions = {
  ignore: "保留异常但不再计入待处理数量，后续可重新打开。",
  reopen: "将已忽略异常重新置为待处理。",
  restore_source: "把回收区中的物理文件恢复到登记的正式存储位置。",
  move_to_trash: "把正式存储区中的回收文件移入登记的回收区位置。",
  move_to_expected_root: "把物理文件移动到文件信息表登记的存储区域。",
  quarantine_file: "把异常副本或孤立文件移入不可公开访问的隔离区。",
  restore_quarantine: "把隔离文件恢复到隔离前的位置，异常会重新打开。",
  delete_quarantine: "永久删除隔离文件，此操作不可恢复。",
  accept_current: "以当前物理文件重新计算大小和摘要并更新文件信息表。",
  register_orphan: "把孤立物理文件登记为当前管理员所有的有效文件。"
};

function open() {
  visible.value = true;
  activeTab.value = "issues";
  refreshAll();
  startPolling();
}

function refreshAll() {
  getStats();
  getIssueList();
  getRunList();
}

function getStats() {
  return getFileReconcileStats().then(response => {
    Object.assign(stats, response.data);
  });
}

function getIssueList() {
  issueLoading.value = true;
  return listFileReconcileIssue(issueQuery)
    .then(response => {
      issueList.value = response.rows;
      issueTotal.value = response.total;
    })
    .finally(() => {
      issueLoading.value = false;
    });
}

function getRunList() {
  runLoading.value = true;
  return listFileReconcileRun(runQuery)
    .then(response => {
      runList.value = response.rows;
      runTotal.value = response.total;
    })
    .finally(() => {
      runLoading.value = false;
    });
}

function handleStart() {
  scanning.value = true;
  startFileReconcile({ checkHash: checkHash.value })
    .then(() => {
      feedback.msgSuccess("文件存储对账任务已启动");
      refreshAll();
    })
    .finally(() => {
      scanning.value = false;
    });
}

function handleIssueQuery() {
  issueQuery.pageNum = 1;
  getIssueList();
}

function resetIssueQuery() {
  issueQueryRef.value?.resetFields();
  issueQuery.pageNum = 1;
  issueQuery.status = "open";
  getIssueList();
}

function handleTabChange(tabName) {
  if (tabName === "runs") {
    getRunList();
  }
}

function handleCommand(action, row) {
  currentIssueId.value = row.issueId;
  Object.assign(handleForm, {
    action,
    reason: "",
    originalName: row.originalName || row.actualKey?.split("/").pop() || ""
  });
  handleOpen.value = true;
  nextTick(() => handleFormRef.value?.clearValidate());
}

function submitHandle() {
  handleFormRef.value?.validateFields().then(() => {
    handling.value = true;
    handleFileReconcileIssue(currentIssueId.value, {
      action: handleForm.action,
      reason: handleForm.reason,
      originalName:
        handleForm.action === "register_orphan"
          ? handleForm.originalName
          : undefined
    })
      .then(() => {
        feedback.msgSuccess("文件存储异常处理成功");
        handleOpen.value = false;
        refreshAll();
        emit("refresh");
      })
      .finally(() => {
        handling.value = false;
      });
  }).catch(() => {});
}

function startPolling() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (!visible.value || stats.latestRun?.status !== "running") return;
    refreshAll();
  }, 3000);
}

function handleClosed() {
  clearInterval(pollTimer);
  pollTimer = undefined;
  handleOpen.value = false;
}
function closeDrawer() { visible.value = false; handleClosed(); }

function formatLocation(root, key) {
  return root && key ? `${root}:${key}` : "-";
}

function formatOptionalSize(size) {
  return size === null || size === undefined ? "-" : formatFileSize(size);
}

function issueTypeLabel(type) {
  return issueTypeOptions.find(item => item.value === type)?.label || type;
}

function severityLabel(severity) {
  return { critical: "严重", warning: "警告", info: "提示" }[severity] || severity;
}

function severityTagType(severity) {
  return { critical: "danger", warning: "warning", info: "info" }[severity] || "info";
}

function issueStatusLabel(status) {
  return {
    open: "待处理",
    ignored: "已忽略",
    quarantined: "已隔离",
    resolved: "已解决"
  }[status] || status;
}

function issueStatusType(status) {
  return {
    open: "danger",
    ignored: "info",
    quarantined: "warning",
    resolved: "success"
  }[status] || "info";
}

function runStatusLabel(status) {
  return {
    running: "运行中",
    completed: "已完成",
    failed: "失败"
  }[status] || "尚未执行";
}

function runStatusType(status) {
  return {
    running: "warning",
    completed: "success",
    failed: "danger"
  }[status] || "info";
}

function actionLabel(action) {
  return {
    ignore: "忽略异常",
    reopen: "重新打开",
    restore_source: "恢复到正式区",
    move_to_trash: "移入回收区",
    move_to_expected_root: "移到预期区域",
    quarantine_file: "隔离文件",
    restore_quarantine: "恢复隔离文件",
    delete_quarantine: "永久删除隔离文件",
    accept_current: "接受当前文件",
    register_orphan: "登记孤立文件"
  }[action] || action;
}

onBeforeUnmount(() => clearInterval(pollTimer));

defineExpose({ open });
</script>

<style scoped>
.reconcile-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  position: relative;
  overflow: hidden;
  min-height: 112px;
  padding: 16px 18px;
  border: 1px solid var(--workspace-border);
  border-radius: 8px;
  background: var(--workspace-surface);
}

.summary-card::before {
  position: absolute;
  top: 0;
  left: 0;
  width: 4px;
  height: 100%;
  content: "";
  background: var(--card-color);
}

.summary-card--danger {
  --card-color: #cf1322;
}

.summary-card--critical {
  --card-color: #b42318;
}

.summary-card--warning {
  --card-color: #d48806;
}

.summary-card--quarantine {
  --card-color: #7f56d9;
}

.summary-card--run {
  --card-color: var(--workspace-main);
}

.summary-card__label {
  color: var(--workspace-text-secondary);
  font-size: 14px;
}

.summary-card__value {
  margin-top: 7px;
  color: var(--workspace-text);
  font-size: 28px;
  font-weight: 600;
  line-height: 34px;
}

.summary-card__status {
  display: flex;
  align-items: center;
  height: 41px;
}

.summary-card__hint {
  overflow: hidden;
  color: var(--workspace-text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reconcile-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 10px 14px;
  border-radius: 6px;
  background: var(--workspace-surface-muted);
}

.reconcile-toolbar__tip {
  color: var(--workspace-text-secondary);
  font-size: 13px;
}

.reconcile-toolbar__actions {
  display: flex;
  align-items: center;
  white-space: nowrap;
}

.reconcile-action-trigger {
  display: inline-flex;
  align-items: center;
}

.cell-secondary {
  overflow: hidden;
  margin-top: 3px;
  color: var(--workspace-text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1400px) {
  .reconcile-summary {
    grid-template-columns: repeat(3, minmax(160px, 1fr));
  }
}
</style>
