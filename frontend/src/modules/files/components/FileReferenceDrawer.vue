<!-- Adapted from original FileReferenceDrawer, MIT licensed. -->
<script setup lang="ts">
import { Alert as AAlert, Tag as ATag } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import ResourceTable from '../../../shared/components/ResourceTable.vue'
import { getFileReferences } from '../api/index.ts'
import type { FileReference } from '../api/index.ts'
import { useFilePanel } from '../composables/useFilePanel.ts'
import { formatTime } from '../presentation.ts'
const { visible, target, loading, error, data, open, close } = useFilePanel(getFileReferences)
const columns: TableColumnsType<FileReference> = [
  { title: '业务类型', dataIndex: 'resourceType', key: 'resourceType', width: 130, ellipsis: true },
  { title: '业务ID', dataIndex: 'resourceId', key: 'resourceId', width: 200, ellipsis: true },
  { title: '保留到期', key: 'retainUntil', width: 180 },
  { title: '来源', key: 'source', width: 100 },
  { title: '登记时间', key: 'createdAt', width: 180 }
]
defineExpose({ open })
</script>
<template>
  <ResourceDrawer
    :title="`业务引用 - ${target?.displayName ?? ''}`"
    :open="visible"
    width="55%"
    @close="close"
  >
    <AAlert
      message="存在业务引用时文件不能移入回收站，请先在对应业务中解除文件引用。"
      type="warning"
      :closable="false"
      show-icon
      class="mb8"
    />
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <ResourceTable
      :columns="columns"
      :rows="data ?? []"
      :loading="loading"
      row-key="id"
      :scroll-x="900"
    >
      <template #bodyCell="{ column, record: row }"
        ><template v-if="column.key === 'retainUntil'">{{
          row.retainUntil ? formatTime(row.retainUntil) : '永久保留'
        }}</template
        ><ATag v-else-if="column.key === 'source'" color="green">引用关系</ATag
        ><template v-else-if="column.key === 'createdAt'">{{
          formatTime(row.createdAt)
        }}</template></template
      >
    </ResourceTable>
  </ResourceDrawer>
</template>
