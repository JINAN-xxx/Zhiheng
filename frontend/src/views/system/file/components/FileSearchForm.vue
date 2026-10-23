<template>
  <a-form
    ref="queryRef"
    v-show="show"
    :model="queryParams"
    layout="inline"
    class="file-search-form"
  >
    <a-form-item label="文件名称" name="originalName">
      <a-input
        v-model:value="queryParams.originalName"
        placeholder="请输入原始文件名"
        allow-clear
        style="width: 200px"
        @press-enter="emit('query')"
      />
    </a-form-item>
    <a-form-item label="访问类型" name="accessType">
      <a-select
        v-model:value="queryParams.accessType"
        placeholder="请选择访问类型"
        allow-clear
        style="width: 200px"
        :options="accessTypeOptions"
      />
    </a-form-item>
    <a-form-item label="文件状态" name="status">
      <a-select
        v-model:value="queryParams.status"
        placeholder="请选择文件状态"
        allow-clear
        style="width: 200px"
        :options="statusOptions"
      />
    </a-form-item>
    <a-form-item label="上传用户" name="createBy">
      <a-input
        v-model:value="queryParams.createBy"
        placeholder="请输入上传用户"
        allow-clear
        style="width: 200px"
        @press-enter="emit('query')"
      />
    </a-form-item>
    <a-form-item label="所有者" name="ownerName">
      <a-input
        v-model:value="queryParams.ownerName"
        placeholder="请输入所有者"
        allow-clear
        style="width: 200px"
        @press-enter="emit('query')"
      />
    </a-form-item>
    <a-form-item label="所属部门" name="deptId">
      <a-tree-select
        v-model:value="queryParams.deptId"
        :tree-data="deptOptions"
        :field-names="{ value: 'id', label: 'label', children: 'children' }"
        placeholder="请选择所属部门"
        show-search
        allow-clear
        tree-default-expand-all
        style="width: 200px"
      />
    </a-form-item>
    <a-form-item label="过期状态" name="expirationStatus">
      <a-select
        v-model:value="queryParams.expirationStatus"
        placeholder="请选择过期状态"
        allow-clear
        style="width: 200px"
        :options="expirationOptions"
      />
    </a-form-item>
    <a-form-item label="上传时间">
      <a-range-picker
        v-model:value="dateRange"
        value-format="YYYY-MM-DD HH:mm:ss"
        show-time
        style="width: 200px"
        :placeholder="['开始时间', '结束时间']"
      />
    </a-form-item>
    <a-form-item><a-space><a-button type="primary" @click="emit('query')"><SearchOutlined />搜索</a-button><a-button @click="handleReset"><ReloadOutlined />重置</a-button></a-space></a-form-item>
  </a-form>
</template>

<script setup>
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
const accessTypeOptions = [{ label: '公开文件', value: 'public' }, { label: '受保护文件', value: 'private' }]
const statusOptions = [{ label: '正常', value: 'active' }, { label: '已删除', value: 'deleted' }, { label: '清理中', value: 'purging' }]
const expirationOptions = [{ label: '永久有效', value: 'permanent' }, { label: '有效', value: 'valid' }, { label: '7天内过期', value: 'expiring' }, { label: '已过期', value: 'expired' }]
defineProps({
  show: {
    type: Boolean,
    default: true
  },
  deptOptions: {
    type: Array,
    default: () => []
  }
});

const emit = defineEmits(["query", "reset"]);
const queryParams = defineModel("queryParams", {
  type: Object,
  required: true
});
const dateRange = defineModel("dateRange", {
  type: Array,
  default: () => []
});
const queryRef = ref();

function handleReset() {
  dateRange.value = [];
  queryRef.value?.resetFields();
  emit("reset");
}
</script>

<style scoped>
.file-search-form { margin-bottom: 16px; row-gap: 8px; }
@media (max-width: 767px) { .file-search-form { display: grid; } .file-search-form :deep(.ant-form-item), .file-search-form :deep(.ant-input), .file-search-form :deep(.ant-select), .file-search-form :deep(.ant-picker) { width: 100% !important; } }
</style>
