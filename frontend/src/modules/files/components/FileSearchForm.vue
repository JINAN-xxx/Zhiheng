<!-- Adapted from original FileSearchForm, MIT licensed. -->
<template>
  <a-form v-show="show" :model="queryParams" layout="vertical" class="file-search-form">
    <a-form-item label="文件名称" name="originalName">
      <a-input
        v-model:value="queryParams.originalName"
        placeholder="请输入原始文件名"
        allow-clear
        @press-enter="emit('query')"
      />
    </a-form-item>
    <a-form-item label="访问类型" name="accessType">
      <a-select
        :value="queryParams.accessType || undefined"
        placeholder="请选择访问类型"
        allow-clear
        :options="accessTypeOptions"
        @update:value="setAccess"
      />
    </a-form-item>
    <a-form-item label="文件状态" name="status">
      <a-select
        :value="queryParams.status || undefined"
        placeholder="请选择文件状态"
        allow-clear
        :options="statusOptions"
        @update:value="setStatus"
      />
    </a-form-item>
    <a-form-item label="上传用户" name="uploaderSearch">
      <a-input
        v-model:value="queryParams.uploaderSearch"
        placeholder="请输入上传用户"
        allow-clear
        @press-enter="emit('query')"
      />
    </a-form-item>
    <a-form-item label="所有者" name="ownerSearch">
      <a-input
        v-model:value="queryParams.ownerSearch"
        placeholder="请输入所有者"
        allow-clear
        @press-enter="emit('query')"
      />
    </a-form-item>
    <a-form-item label="过期状态" name="expirationStatus">
      <a-select
        :value="queryParams.expirationStatus || undefined"
        placeholder="请选择过期状态"
        allow-clear
        :options="expirationOptions"
        @update:value="setExpiration"
      />
    </a-form-item>
    <a-form-item label="文件类型" name="mime"
      ><a-input
        v-model:value="queryParams.mime"
        aria-label="筛选 MIME 类型"
        placeholder="例如 text/plain"
        allow-clear
        @press-enter="emit('query')"
    /></a-form-item>
    <a-form-item label="上传时间">
      <a-range-picker
        v-bind="dateRange ? { value: dateRange } : {}"
        value-format="YYYY-MM-DD HH:mm:ss"
        show-time
        :placeholder="['开始时间', '结束时间']"
        @update:value="setDates"
      />
    </a-form-item>
    <a-form-item
      ><a-space
        ><a-button aria-label="搜索文件" type="primary" @click="emit('query')"
          ><SearchOutlined />搜索</a-button
        ><a-button aria-label="重置筛选" @click="handleReset"
          ><ReloadOutlined />重置</a-button
        ></a-space
      ></a-form-item
    >
  </a-form>
</template>

<script setup lang="ts">
import {
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Select as ASelect,
  RangePicker as ARangePicker,
  Button as AButton,
  Space as ASpace
} from 'ant-design-vue'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { emptyQuery } from '../api/index.ts'
import type { FileQuery } from '../api/index.ts'
const accessTypeOptions = [
  { label: '平台内公开', value: 'public' },
  { label: '受保护文件', value: 'private' }
]
const statusOptions = [
  { label: '正常', value: 'active' },
  { label: '已删除', value: 'deleted' },
  { label: '清理中', value: 'purging' },
  { label: '已清理', value: 'purged' }
]
const expirationOptions = [
  { label: '永久有效', value: 'permanent' },
  { label: '有效', value: 'valid' },
  { label: '7天内过期', value: 'expiring' },
  { label: '已过期', value: 'expired' }
]
defineProps<{ show: boolean }>()
const emit = defineEmits<{ query: []; reset: [] }>()
const queryParams = defineModel<FileQuery>('queryParams', { required: true })
const dateRange = defineModel<[string, string] | null>('dateRange', { required: true })
function setAccess(value: unknown): void {
  queryParams.value.accessType = value === 'public' || value === 'private' ? value : ''
}
function setStatus(value: unknown): void {
  queryParams.value.status =
    value === 'active' || value === 'deleted' || value === 'purging' || value === 'purged'
      ? value
      : ''
}
function setExpiration(value: unknown): void {
  queryParams.value.expirationStatus =
    value === 'permanent' || value === 'valid' || value === 'expiring' || value === 'expired'
      ? value
      : ''
}
function setDates(value: unknown): void {
  dateRange.value =
    Array.isArray(value) && typeof value[0] === 'string' && typeof value[1] === 'string'
      ? [value[0], value[1]]
      : null
}
function handleReset(): void {
  Object.assign(queryParams.value, emptyQuery())
  dateRange.value = null
  emit('reset')
}
</script>

<style scoped>
.file-search-form {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px 16px;
  margin-bottom: 18px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--gray-150);
}
.file-search-form :deep(.ant-form-item) {
  min-width: 0;
  margin: 0;
}
.file-search-form :deep(.ant-form-item-label) {
  padding-bottom: 6px;
}
.file-search-form :deep(.ant-form-item-label > label) {
  color: var(--color-text-secondary);
  font-size: 13px;
}
.file-search-form :deep(.ant-select),
.file-search-form :deep(.ant-picker) {
  width: 100%;
}
.file-search-form :deep(.ant-form-item:last-child) {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  align-items: flex-end;
}
@media (max-width: 1120px) {
  .file-search-form {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .file-search-form {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 480px) {
  .file-search-form {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
