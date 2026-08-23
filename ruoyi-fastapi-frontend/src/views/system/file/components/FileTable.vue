<template>
  <ResourceTable :columns="columns" :rows="fileList" :loading="loading" :pagination="false" row-key="fileId" :scroll="{ x: 1750 }">
    <template #headerCell="{ column }"><a-checkbox v-if="column.key === 'selection'" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" /></template>
    <template #bodyCell="{ column, record: row }">
      <a-checkbox v-if="column.key === 'selection'" :checked="selectedIds.includes(row.fileId)" @change="event => toggleRow(row,event.target.checked)" />
      <a-tag v-else-if="column.key === 'accessType'" :color="row.accessType === 'public' ? 'green' : 'orange'">{{ row.accessType === 'public' ? '公开' : '受保护' }}</a-tag>
      <template v-else-if="column.key === 'fileSize'">{{ formatFileSize(row.fileSize) }}</template><template v-else-if="column.key === 'ownerName'">{{ row.ownerName || row.ownerUserId || '-' }}</template><template v-else-if="column.key === 'deptName'">{{ row.deptName || row.deptId || '-' }}</template>
      <a-tag v-else-if="column.key === 'expireTime'" :color="tagColor(expirationTagType(row.expireTime))">{{ expirationLabel(row.expireTime) }}</a-tag>
      <template v-else-if="column.key === 'acl'"><a-tooltip v-if="row.aclNearestExpireTime" :title="`最近过期：${parseTime(row.aclNearestExpireTime)}`"><a-tag :color="isAclExpiring(row.aclNearestExpireTime) ? 'orange' : 'default'">{{ row.aclEntryCount }} 项<span v-if="isAclExpiring(row.aclNearestExpireTime)"> · 即将过期</span></a-tag></a-tooltip><span v-else>{{ row.aclEntryCount || 0 }} 项</span></template>
      <template v-else-if="column.key === 'reference'"><a-button v-if="row.referenceCount" type="link" @click="emit('reference',row)">{{ row.referenceCount }} 项</a-button><span v-else>0 项</span></template>
      <a-tag v-else-if="column.key === 'storageStatus'" :color="tagColor(storageStatusTagType(row.storageStatus))">{{ storageStatusLabel(row.storageStatus) }}</a-tag>
      <template v-else-if="column.key === 'createTime'">{{ parseTime(row.createTime) }}</template>
      <template v-else-if="column.key === 'status'"><a-tag :color="row.status === 'active' ? 'green' : row.status === 'deleted' ? 'default' : 'orange'">{{ row.status === 'active' ? '正常' : row.status === 'deleted' ? '已删除' : '清理中' }}</a-tag></template>
      <a-space v-else-if="column.key === 'actions'" :size="2">
        <a-tooltip title="详细"><a-button type="link" @click="emit('view',row)" v-hasPermi="['system:file:query']"><EyeOutlined /></a-button></a-tooltip>
        <a-tooltip v-if="row.status === 'active'" title="下载"><a-button type="link" @click="emit('download',row)" v-hasPermi="['system:file:download']"><DownloadOutlined /></a-button></a-tooltip>
        <a-tooltip v-if="row.accessType === 'private' && row.status === 'active'" title="授权"><a-button type="link" @click="emit('acl',row)" v-hasPermi="['system:file:edit']"><LockOutlined /></a-button></a-tooltip>
        <a-tooltip v-if="row.status === 'active'" title="转移"><a-button type="link" @click="emit('transfer',row)" v-hasPermi="['system:file:transfer']"><SwapOutlined /></a-button></a-tooltip>
        <a-tooltip title="审计"><a-button type="link" @click="emit('audit',row)" v-hasPermi="['system:file:query']"><AuditOutlined /></a-button></a-tooltip>
        <a-tooltip v-if="row.status === 'active' && row.referenceCount" title="存在业务引用，无法删除"><a-button type="link" danger disabled v-hasPermi="['system:file:remove']"><DeleteOutlined /></a-button></a-tooltip>
        <a-tooltip v-else-if="row.status === 'active'" title="删除"><a-button type="link" danger @click="emit('delete',row)" v-hasPermi="['system:file:remove']"><DeleteOutlined /></a-button></a-tooltip>
        <a-tooltip v-if="row.status === 'deleted'" title="恢复"><a-button type="link" @click="emit('restore',row)" v-hasPermi="['system:file:restore']"><RollbackOutlined /></a-button></a-tooltip>
        <a-tooltip v-if="row.status !== 'active'" :title="row.status === 'purging' ? '重试永久清理' : '永久清理'"><a-button type="link" danger @click="emit('purge',row)" v-hasPermi="['system:file:purge']"><DeleteFilled /></a-button></a-tooltip>
      </a-space>
    </template>
  </ResourceTable>
</template>

<script setup>
import { AuditOutlined, DeleteFilled, DeleteOutlined, DownloadOutlined, EyeOutlined, LockOutlined, RollbackOutlined, SwapOutlined } from '@ant-design/icons-vue'
import ResourceTable from '@/components/Antd/ResourceTable.vue'
import {
  expirationLabel,
  expirationTagType,
  formatFileSize,
  isAclExpiring,
  storageStatusLabel,
  storageStatusTagType
} from "./fileFormatters";

const props = defineProps({
  fileList: {
    type: Array,
    default: () => []
  },
  loading: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits([
  "selection-change",
  "view",
  "download",
  "reference",
  "acl",
  "transfer",
  "audit",
  "delete",
  "restore",
  "purge"
]);
const selectedIds = ref([]);
const selectedRows = computed(() => props.fileList.filter(row => selectedIds.value.includes(row.fileId)));
const allSelected = computed(() => props.fileList.length > 0 && props.fileList.every(row => selectedIds.value.includes(row.fileId)));
const someSelected = computed(() => selectedIds.value.length > 0 && !allSelected.value);
const columns = [
  {title:'',key:'selection',width:48},{title:'原始文件名',dataIndex:'originalName',key:'originalName',width:220,ellipsis:true},{title:'访问类型',key:'accessType',width:110},{title:'扩展名',dataIndex:'extension',key:'extension',width:90},{title:'文件大小',key:'fileSize',width:120},{title:'上传用户',dataIndex:'createBy',key:'createBy',width:120,ellipsis:true},{title:'所有者',key:'ownerName',width:120},{title:'所属部门',key:'deptName',width:140},{title:'文件有效期',key:'expireTime',width:150},{title:'ACL',key:'acl',width:130},{title:'业务引用',key:'reference',width:100},{title:'存储状态',key:'storageStatus',width:110},{title:'上传时间',key:'createTime',width:180},{title:'状态',key:'status',width:90},{title:'操作',key:'actions',width:300,fixed:'right'},
];
const tagColor = type => ({success:'green',warning:'orange',danger:'red',info:'default',primary:'blue'}[type] || type);
function notifySelection() { emit('selection-change', selectedRows.value); }
function toggleRow(row, checked) { selectedIds.value = checked ? [...new Set([...selectedIds.value,row.fileId])] : selectedIds.value.filter(id => id !== row.fileId); notifySelection(); }
function toggleAll(event) { selectedIds.value = event.target.checked ? props.fileList.map(row => row.fileId) : []; notifySelection(); }
watch(() => props.fileList, () => { selectedIds.value = []; notifySelection(); });
</script>

<style scoped>
.file-action-disabled-wrapper {
  display: inline-flex;
  align-items: center;
  margin-left: 12px;
  vertical-align: middle;
}
</style>
