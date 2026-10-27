<!-- Adapted from original ResourceTable.vue, MIT licensed. -->
<script setup lang="ts" generic="T extends object">
import { Table as ATable } from 'ant-design-vue'
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue'
defineProps<{
  columns: TableColumnsType<T>
  rows: T[]
  loading?: boolean
  scrollX?: number | string
  rowKey?: string | ((record: T) => string)
  pagination?: false | TablePaginationConfig
  emptyText?: string
}>()
defineSlots<{
  bodyCell(props: { column: { key?: unknown }; record: T; index: number }): unknown
  headerCell(props: { column: { key?: unknown } }): unknown
}>()
</script>
<template>
  <div class="antd-resource-table">
    <ATable
      :columns="columns"
      :data-source="rows"
      :loading="loading ?? false"
      :pagination="pagination ?? false"
      v-bind="emptyText ? { locale: { emptyText } } : {}"
      :row-key="rowKey ?? 'id'"
      :scroll="{ x: scrollX ?? 850 }"
      size="middle"
      ><template v-if="$slots.headerCell" #headerCell="props"
        ><slot name="headerCell" :column="props.column" /></template
      ><template #bodyCell="props"
        ><slot
          name="bodyCell"
          :column="props.column"
          :record="props.record as T"
          :index="props.index" /></template
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
