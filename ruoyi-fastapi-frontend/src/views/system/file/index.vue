<template>
  <WorkspacePage title="文件管理" subtitle="文件生命周期、访问控制、引用与存储治理">
    <file-search-form v-model:query-params="queryParams" v-model:date-range="dateRange" :show="showSearch" :dept-options="fileDeptOptions" @query="handleQuery" @reset="resetQuery" />
    <file-statistics :stats="fileStats" />
    <div class="table-toolbar mb8">
      <a-button type="primary" :disabled="privateMultiple" @click="handleAcl()" v-hasPermi="['system:file:edit']"><LockOutlined />授权</a-button>
      <a-button :disabled="activeMultiple" @click="handleTransfer()" v-hasPermi="['system:file:transfer']"><SwapOutlined />转移</a-button>
      <a-button :disabled="restoreMultiple" @click="handleRestore()" v-hasPermi="['system:file:restore']"><RollbackOutlined />恢复</a-button>
      <a-button danger :disabled="purgeMultiple" @click="handlePurge()" v-hasPermi="['system:file:purge']"><DeleteFilled />清理</a-button>
      <a-tooltip :title="selectedReferencedCount ? '所选文件存在业务引用，请先解除引用' : undefined"><span><a-button danger :disabled="deleteMultiple" @click="handleDelete()" v-hasPermi="['system:file:remove']"><DeleteOutlined />删除</a-button></span></a-tooltip>
      <a-button @click="retentionPolicyDrawerRef?.open()" v-hasPermi="['system:file:edit']"><FieldTimeOutlined />策略</a-button>
      <a-button @click="reconcileDrawerRef?.open()" v-hasPermi="['system:file:reconcile']"><PartitionOutlined />对账</a-button>
      <a-button @click="retentionReminderDrawerRef?.open()" v-hasPermi="['system:file:list']"><BellOutlined />提醒</a-button>
      <span class="toolbar-spacer" /><a-tooltip title="显示或隐藏筛选"><a-button aria-label="显示或隐藏筛选" @click="showSearch = !showSearch"><FilterOutlined /></a-button></a-tooltip><a-tooltip title="刷新"><a-button aria-label="刷新" :loading="loading" @click="getList"><ReloadOutlined /></a-button></a-tooltip>
    </div>
    <file-table :file-list="fileList" :loading="loading" @selection-change="handleSelectionChange" @view="handleView" @download="handleDownload" @reference="handleReference" @acl="handleAcl" @transfer="handleTransfer" @audit="handleAudit" @delete="handleDelete" @restore="handleRestore" @purge="handlePurge" />
    <a-pagination v-if="total > 0" v-model:current="queryParams.pageNum" v-model:page-size="queryParams.pageSize" :total="total" show-size-changer @change="getList" />
    <file-detail-dialog v-model="detailOpen" :detail="detail" @reference="handleReference" /><file-reference-drawer ref="referenceDrawerRef" /><file-retention-policy-drawer ref="retentionPolicyDrawerRef" /><file-retention-reminder-drawer ref="retentionReminderDrawerRef" @refresh="getList" /><file-acl-drawer ref="aclDrawerRef" @refresh="getList" /><file-transfer-dialog ref="transferDialogRef" @refresh="getList" /><file-audit-drawer ref="auditDrawerRef" /><file-reconcile-drawer ref="reconcileDrawerRef" @refresh="getList" />
  </WorkspacePage>
</template>


<script setup name="File">
import { BellOutlined, DeleteFilled, DeleteOutlined, FieldTimeOutlined, FilterOutlined, LockOutlined, PartitionOutlined, ReloadOutlined, RollbackOutlined, SwapOutlined } from '@ant-design/icons-vue'
import WorkspacePage from '@/components/Antd/WorkspacePage.vue'
import feedback from '@/plugins/antdFeedback'
import {
  delFile,
  getFile,
  getFileAclDeptTree,
  getFileStats,
  listFile,
  purgeFile,
  restoreFile
} from "@/api/system/file";
import FileAclDrawer from "./components/FileAclDrawer.vue";
import FileAuditDrawer from "./components/FileAuditDrawer.vue";
import FileDetailDialog from "./components/FileDetailDialog.vue";
import FileReferenceDrawer from "./components/FileReferenceDrawer.vue";
import FileReconcileDrawer from "./components/FileReconcileDrawer.vue";
import FileRetentionPolicyDrawer from "./components/FileRetentionPolicyDrawer.vue";
import FileRetentionReminderDrawer from "./components/FileRetentionReminderDrawer.vue";
import FileSearchForm from "./components/FileSearchForm.vue";
import FileStatistics from "./components/FileStatistics.vue";
import FileTable from "./components/FileTable.vue";
import FileTransferDialog from "./components/FileTransferDialog.vue";

const { proxy } = getCurrentInstance();
const fileList = ref([]);
const loading = ref(true);
const showSearch = ref(true);
const ids = ref([]);
const privateIds = ref([]);
const activeMultiple = ref(true);
const deleteMultiple = ref(true);
const privateMultiple = ref(true);
const restoreMultiple = ref(true);
const purgeMultiple = ref(true);
const selectedReferencedCount = ref(0);
const total = ref(0);
const dateRange = ref([]);
const detailOpen = ref(false);
const detail = ref({});
const fileDeptOptions = ref([]);
const referenceDrawerRef = ref();
const retentionPolicyDrawerRef = ref();
const retentionReminderDrawerRef = ref();
const aclDrawerRef = ref();
const transferDialogRef = ref();
const auditDrawerRef = ref();
const reconcileDrawerRef = ref();
const fileStats = reactive({
  totalCount: 0,
  totalSize: 0,
  publicSize: 0,
  privateSize: 0,
  activeCount: 0,
  deletedCount: 0,
  expiredCount: 0,
  retentionExpiringCount: 0,
  aclExpiringCount: 0
});
const queryParams = reactive({
  pageNum: 1,
  pageSize: 10,
  originalName: undefined,
  accessType: undefined,
  status: "active",
  createBy: undefined,
  ownerName: undefined,
  deptId: undefined,
  expirationStatus: undefined
});

/** 查询文件列表 */
function getList() {
  loading.value = true;
  const query = proxy.addDateRange({ ...queryParams }, dateRange.value);
  listFile(query)
    .then(response => {
      fileList.value = response.rows;
      total.value = response.total;
    })
    .finally(() => {
      loading.value = false;
    });
  getFileStats(query).then(response => {
    Object.assign(fileStats, response.data);
  });
}

/** 搜索按钮操作 */
function handleQuery() {
  queryParams.pageNum = 1;
  getList();
}

/** 重置按钮操作 */
function resetQuery() {
  queryParams.pageNum = 1;
  getList();
}

/** 多选框选中数据 */
function handleSelectionChange(selection) {
  ids.value = selection.map(item => item.fileId);
  privateIds.value = selection
    .filter(item => item.status === "active" && item.accessType === "private")
    .map(item => item.fileId);
  selectedReferencedCount.value = selection.filter(
    item => item.referenceCount > 0
  ).length;
  activeMultiple.value =
    !selection.length || selection.some(item => item.status !== "active");
  deleteMultiple.value =
    activeMultiple.value || selectedReferencedCount.value > 0;
  privateMultiple.value = !privateIds.value.length;
  restoreMultiple.value =
    !selection.length || selection.some(item => item.status !== "deleted");
  purgeMultiple.value =
    !selection.length ||
    selection.some(item => !["deleted", "purging"].includes(item.status));
}

/** 查看文件详情 */
function handleView(row) {
  getFile(row.fileId).then(response => {
    detail.value = response.data;
    detailOpen.value = true;
  });
}

/** 下载文件 */
function handleDownload(row) {
  const displayName = encodeURIComponent(row.storedName || "file");
  proxy.$download.file(
    `/system/file/download/${row.fileId}/${displayName}`
  );
}

/** 查看文件业务引用 */
function handleReference(row) {
  referenceDrawerRef.value?.open(row);
}

/** 配置文件访问权限 */
function handleAcl(row) {
  aclDrawerRef.value?.open(row, ids.value, privateIds.value);
}

/** 转移文件 */
function handleTransfer(row) {
  transferDialogRef.value?.open(row, ids.value);
}

/** 查看文件访问审计 */
function handleAudit(row) {
  auditDrawerRef.value?.open(row);
}

/** 删除文件 */
function handleDelete(row) {
  const isSingle = row?.fileId;
  if (isSingle && row.referenceCount > 0) {
    feedback.msgWarning("文件仍被业务引用，请先解除引用后再删除");
    return;
  }
  if (!isSingle && selectedReferencedCount.value > 0) {
    feedback.msgWarning("所选文件存在业务引用，请先解除引用后再删除");
    return;
  }
  const fileIds = isSingle ? row.fileId : ids.value;
  const fileName = isSingle ? row.originalName : `${ids.value.length}个文件`;
  feedback
    .confirm(`是否确认将文件“${fileName}”移入回收站?`)
    .then(() => delFile(fileIds))
    .then(() => {
      getList();
      feedback.msgSuccess("文件已移入回收站");
    })
    .catch(() => {});
}

/** 恢复文件 */
function handleRestore(row) {
  const isSingle = row?.fileId;
  const fileIds = isSingle ? row.fileId : ids.value;
  const fileName = isSingle ? row.originalName : `${ids.value.length}个文件`;
  feedback
    .confirm(`是否确认恢复文件“${fileName}”?`)
    .then(() => restoreFile(fileIds))
    .then(() => {
      getList();
      feedback.msgSuccess("文件恢复成功");
    })
    .catch(() => {});
}

/** 永久清理文件 */
function handlePurge(row) {
  const isSingle = row?.fileId;
  const fileIds = isSingle ? row.fileId : ids.value.join(",");
  const fileName = isSingle ? row.originalName : `${ids.value.length}个文件`;
  feedback
    .confirm(`永久清理后无法恢复，是否确认清理文件“${fileName}”?`)
    .then(() => purgeFile(fileIds))
    .then(() => {
      getList();
      feedback.msgSuccess("文件已永久清理");
    })
    .catch(() => {});
}

getList();
getFileAclDeptTree().then(response => {
  fileDeptOptions.value = response.data;
});
</script>

<style scoped>
.table-toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:12px; }
.toolbar-spacer { flex:1 1 auto; }
:deep(.ant-pagination) { margin-top:16px; }
</style>
