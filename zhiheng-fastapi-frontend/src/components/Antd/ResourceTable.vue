<template>
  <div class="antd-resource-table">
    <a-table
      :columns="visibleColumns"
      :data-source="rows"
      :loading="loading"
      :pagination="pagination"
      :row-key="rowKey"
      :scroll="scroll"
      size="middle"
      @change="(...args) => emit('change', ...args)"
    >
      <template v-for="(_, slotName) in $slots" #[slotName]="slotProps" :key="slotName">
        <slot :name="slotName" v-bind="slotProps ?? {}" />
      </template>
    </a-table>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWindowSize } from '@vueuse/core'

const props = defineProps({
  columns: { type: Array, default: () => [] },
  rows: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  pagination: { type: [Boolean, Object], default: false },
  rowKey: { type: [String, Function], default: 'id' },
  scroll: { type: Object, default: undefined },
})

const emit = defineEmits(['change'])
const { width } = useWindowSize()
const visibleColumns = computed(() => width.value < 768
  ? props.columns.filter((column) => !column.mobileHidden)
  : props.columns)
</script>

<style scoped>
.antd-resource-table { min-width: 0; }
.antd-resource-table :deep(.ant-table-wrapper),
.antd-resource-table :deep(.ant-spin-nested-loading) { min-width: 0; }
.antd-resource-table :deep(.ant-table) { font-size: 14px; }
.antd-resource-table :deep(.ant-table-thead > tr > th) {
  min-height: 48px;
  font-size: 13px;
  line-height: 24px;
}
.antd-resource-table :deep(.ant-table-tbody > tr) { height: 48px; }
.antd-resource-table :deep(.ant-table-tbody > tr > td) {
  min-height: 48px;
  line-height: 24px;
}
</style>
