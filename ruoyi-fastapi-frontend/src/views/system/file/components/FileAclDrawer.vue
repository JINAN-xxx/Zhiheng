<template>
  <ResourceDrawer :open="visible" :title="drawerTitle" width="70%" @close="closeDrawer">
    <a-alert :message="batchMode ? '批量操作仅覆盖所选文件的显式授权；内置权限仍按各文件规则生效。' : '内置权限只读展示；管理员和所有者不可被拒绝，启用的上传人权限可被匹配的显式拒绝覆盖。'" type="info" :closable="false" show-icon class="mb8" />
    <div v-if="!batchMode" class="permission-section">
      <div class="permission-section__title">内置权限</div>
      <ResourceTable :columns="builtinColumns" :rows="builtinPermissions" :loading="loading" :pagination="false" :row-key="(row, index) => `${row.source}-${row.subjectId || index}`" :scroll="{ x: 850 }">
        <template #bodyCell="{ column, record: row }">
          <template v-if="column.key === 'source'"><a-tag :color="tagColor(builtinSourceType(row.source))">{{ builtinSourceLabel(row.source) }}</a-tag></template>
          <template v-else-if="column.key === 'subject'">{{ row.subjectName || row.subjectId || '-' }}</template>
          <template v-else-if="column.key === 'enabled'"><a-tag :color="row.enabled ? 'green' : 'default'">{{ row.enabled ? '允许下载' : '已移除' }}</a-tag></template>
          <template v-else-if="column.key === 'deny'"><a-tag v-if="row.enabled" :color="row.denyOverridable ? 'orange' : 'default'">{{ row.denyOverridable ? '可以覆盖' : '不可覆盖' }}</a-tag><span v-else>-</span></template>
        </template>
      </ResourceTable>
    </div>
    <div class="permission-section__header"><span class="permission-section__title">显式授权</span><a-button type="primary" @click="addEntry"><PlusOutlined />添加授权</a-button></div>
    <ResourceTable :columns="entryColumns" :rows="entries" :loading="loading" :pagination="false" :row-key="(_, index) => index" :scroll="{ x: 900 }">
      <template #bodyCell="{ column, record: row, index }">
        <a-select v-if="column.key === 'subjectType'" v-model:value="row.subjectType" :options="subjectTypeOptions" @change="handleSubjectTypeChange(row)" />
        <template v-else-if="column.key === 'subject'">
          <a-tree-select v-if="row.subjectType === 'dept'" v-model:value="row.subjectId" :tree-data="deptOptions" :field-names="{ value: 'id', label: 'label', children: 'children' }" show-search allow-clear tree-default-expand-all placeholder="请选择部门" style="width:100%" />
          <a-select v-else v-model:value="row.subjectId" show-search allow-clear :filter-option="false" :loading="row.subjectLoading" :options="subjectOptions(row)" @search="keyword => searchSubjectOptions(row, keyword)" @dropdown-visible-change="open => handleSubjectVisible(row, open)" placeholder="输入名称搜索" style="width:100%" />
        </template>
        <a-select v-else-if="column.key === 'effect'" v-model:value="row.effect" :options="effectOptions" />
        <template v-else-if="column.key === 'children'"><a-switch v-if="row.subjectType === 'dept'" v-model:checked="row.includeChildren" /><span v-else>-</span></template>
        <a-date-picker v-else-if="column.key === 'expireTime'" v-model:value="row.expireTime" value-format="YYYY-MM-DD HH:mm:ss" show-time placeholder="永久有效" />
        <a-tooltip v-else-if="column.key === 'actions'" title="删除"><a-button type="link" danger aria-label="删除" @click="removeEntry(index)"><DeleteOutlined /></a-button></a-tooltip>
      </template>
    </ResourceTable>
    <template #footer><a-button @click="closeDrawer">取消</a-button><a-button type="primary" :loading="saving" :disabled="loading" @click="submit">保存</a-button></template>
  </ResourceDrawer>
</template>

<script setup>
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue'
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import feedback from '@/plugins/antdFeedback'
import {
  batchSaveFileAcl,
  getFileAclDeptTree,
  listFileAcl,
  saveFileAcl,
  searchFileAclSubjects
} from "@/api/system/file";

const emit = defineEmits(["refresh"]);
const { proxy } = getCurrentInstance();
const visible = ref(false);
const loading = ref(false);
const saving = ref(false);
const fileId = ref("");
const fileName = ref("");
const fileIds = ref("");
const batchMode = ref(false);
const batchCount = ref(0);
const aclVersion = ref(0);
const builtinPermissions = ref([]);
const entries = ref([]);
const deptOptions = ref([]);
let requestGeneration = 0;
const drawerTitle = computed(() =>
  batchMode.value
    ? `批量授权 - ${batchCount.value}个文件`
    : `访问权限 - ${fileName.value}`
);
const builtinColumns = [{ title:'权限来源',key:'source',width:110 },{ title:'权限主体',key:'subject',width:180 },{ title:'规则',key:'enabled',width:110 },{ title:'拒绝覆盖',key:'deny',width:135 },{ title:'权限说明',dataIndex:'description',key:'description',width:320,ellipsis:true }];
const entryColumns = [{ title:'主体类型',key:'subjectType',width:125 },{ title:'授权主体',key:'subject',width:230 },{ title:'规则',key:'effect',width:120 },{ title:'包含下级部门',key:'children',width:125 },{ title:'有效期至',key:'expireTime',width:205 },{ title:'操作',key:'actions',width:70 }];
const subjectTypeOptions = [{label:'用户',value:'user'},{label:'角色',value:'role'},{label:'部门',value:'dept'}];
const effectOptions = [{label:'允许下载',value:'allow'},{label:'拒绝下载',value:'deny'}];
const subjectOptions = row => row.subjectOptions.map(item => ({label:item.subjectName,value:item.subjectId}));
const tagColor = type => ({danger:'red',primary:'blue',warning:'orange',info:'default'}[type] || type);
function closeDrawer() {
  if (saving.value) return;
  requestGeneration += 1;
  visible.value = false;
  loading.value = false;
}

function open(row, selectedIds, selectedPrivateIds) {
  batchMode.value = !row?.fileId;
  if (batchMode.value && !selectedPrivateIds.length) {
    feedback.msgWarning("请选择正常状态的受保护文件");
    return;
  }
  fileId.value = row?.fileId || "";
  fileIds.value = batchMode.value
    ? selectedPrivateIds.join(",")
    : row.fileId;
  batchCount.value = batchMode.value ? selectedPrivateIds.length : 0;
  fileName.value = row?.originalName || "";
  if (batchMode.value && selectedPrivateIds.length < selectedIds.length) {
    feedback.msgWarning(
      `已忽略${selectedIds.length - selectedPrivateIds.length}个公开或非正常状态的文件`
    );
  }
  const generation = ++requestGeneration;
  const requestTarget = fileIds.value;
  visible.value = true;
  loading.value = true;
  builtinPermissions.value = [];
  entries.value = [];
  deptOptions.value = [];
  if (batchMode.value) {
    aclVersion.value = 0;
    getFileAclDeptTree()
      .then(response => {
        if (!isCurrentRequest(generation, requestTarget)) return;
        deptOptions.value = response.data;
      })
      .finally(() => {
        if (generation === requestGeneration) loading.value = false;
      });
    return;
  }
  Promise.all([listFileAcl(row.fileId), getFileAclDeptTree()])
    .then(([aclResponse, deptResponse]) => {
      if (!isCurrentRequest(generation, requestTarget)) return;
      deptOptions.value = deptResponse.data;
      aclVersion.value = aclResponse.data.aclVersion;
      builtinPermissions.value = aclResponse.data.builtinPermissions || [];
      entries.value = aclResponse.data.entries.map(item => ({
        subjectType: item.subjectType,
        subjectId: item.subjectId,
        effect: item.effect,
        includeChildren: item.includeChildren,
        expireTime: item.expireTime,
        subjectLoading: false,
        subjectOptions: [
          { subjectId: item.subjectId, subjectName: item.subjectName }
        ]
      }));
    })
    .finally(() => {
      if (generation === requestGeneration) loading.value = false;
    });
}

function isCurrentRequest(generation, requestTarget) {
  return generation === requestGeneration && visible.value && requestTarget === fileIds.value;
}

function builtinSourceLabel(source) {
  return (
    {
      admin: "平台管理员",
      owner: "文件所有者",
      uploader: "文件上传人"
    }[source] || source
  );
}

function builtinSourceType(source) {
  return (
    {
      admin: "danger",
      owner: "primary",
      uploader: "warning"
    }[source] || "info"
  );
}

function addEntry() {
  entries.value.push({
    subjectType: "user",
    subjectId: undefined,
    effect: "allow",
    includeChildren: false,
    expireTime: undefined,
    subjectLoading: false,
    subjectOptions: []
  });
}

function removeEntry(index) {
  entries.value.splice(index, 1);
}

function handleSubjectTypeChange(row) {
  row.subjectId = undefined;
  row.includeChildren = false;
  row.subjectOptions = [];
}

function handleSubjectVisible(row, visible) {
  if (visible && !row.subjectOptions.length) {
    searchSubjectOptions(row, "");
  }
}

function searchSubjectOptions(row, keyword) {
  const subjectType = row.subjectType;
  row.subjectLoading = true;
  searchFileAclSubjects({ subjectType, keyword })
    .then(response => {
      if (row.subjectType === subjectType) {
        row.subjectOptions = response.data;
      }
      row.subjectLoading = false;
    })
    .catch(() => {
      row.subjectLoading = false;
    });
}

function submit() {
  if (entries.value.some(item => !item.subjectId)) {
    feedback.msgError("请选择完整的授权主体");
    return;
  }
  const aclEntries = entries.value.map(item => ({
    subjectType: item.subjectType,
    subjectId: item.subjectId,
    effect: item.effect,
    includeChildren: item.subjectType === "dept" && item.includeChildren,
    expireTime: item.expireTime || undefined
  }));
  const saveAcl = () => {
    saving.value = true;
    const saveRequest = batchMode.value
      ? batchSaveFileAcl({ fileIds: fileIds.value, entries: aclEntries })
      : saveFileAcl(fileId.value, {
          aclVersion: aclVersion.value,
          entries: aclEntries
        });
    saveRequest
      .then(() => {
        feedback.msgSuccess(
          batchMode.value ? "文件权限批量保存成功" : "文件权限保存成功"
        );
        visible.value = false;
        emit("refresh");
      })
      .finally(() => {
        saving.value = false;
      });
  };
  if (batchMode.value) {
    feedback
      .confirm(`将覆盖所选${batchCount.value}个文件的全部授权，是否继续?`)
      .then(saveAcl)
      .catch(() => {});
  } else {
    saveAcl();
  }
}

defineExpose({ open });
onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped>
.permission-section {
  margin-bottom: 20px;
}

.permission-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 18px 0 8px;
}

.permission-section__title {
  color: var(--workspace-text);
  font-size: 15px;
  font-weight: 600;
}
</style>
