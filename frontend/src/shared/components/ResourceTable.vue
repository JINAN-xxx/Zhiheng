<!-- Adapted from original ResourceTable.vue, MIT licensed. -->
<script setup lang="ts" generic="T extends object">
import { Table as ATable } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
defineProps<{
  columns: TableColumnsType<T>
  rows: T[]
  loading?: boolean
  scrollX?: number | string
}>()
defineSlots<{ bodyCell(props: { column: { key?: unknown }; record: T }): unknown }>()
</script>
<template>
  <div class="antd-resource-table">
    <ATable
      :columns="columns"
      :data-source="rows"
      :loading="loading ?? false"
      :pagination="false"
      row-key="id"
      :scroll="{ x: scrollX ?? 850 }"
      size="middle"
      ><template #bodyCell="props"
        ><slot name="bodyCell" :column="props.column" :record="props.record as T" /></template
    ></ATable>
  </div>
</template>
<style scoped>
.antd-resource-table {
  min-width: 0;
}
.antd-resource-table :deep(.ant-table-wrapper),
.antd-resource-table :deep(.ant-spin-nested-loading) {
  min-width: 0;
}
.antd-resource-table :deep(.ant-table) {
  font-size: var(--workspace-font-size-body);
}
.antd-resource-table :deep(.ant-table-thead > tr > th) {
  min-height: 48px;
  font-size: var(--workspace-font-size-helper);
  line-height: 24px;
}
.antd-resource-table :deep(.ant-table-tbody > tr) {
  height: 48px;
}
.antd-resource-table :deep(.ant-table-tbody > tr > td) {
  min-height: 48px;
  line-height: 24px;
}
</style>
