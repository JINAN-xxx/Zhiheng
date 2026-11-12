<script setup lang="ts">
import {
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import { EllipsisOutlined } from '@ant-design/icons-vue'
export interface RowAction {
  key: string
  label: string
  disabled?: boolean
  danger?: boolean
}
const props = withDefaults(
  defineProps<{ primary?: RowAction | null; more?: RowAction[]; busy?: boolean }>(),
  { primary: null, more: () => [], busy: false }
)
const emit = defineEmits<{ select: [key: string] }>()
function select(key: string) {
  const item = [props.primary, ...props.more].find((item) => item?.key === key)
  if (item && !item.disabled && !props.busy) emit('select', key)
}
</script>
<template>
  <div class="admin-row-actions">
    <AButton
      v-if="primary"
      type="link"
      size="small"
      :danger="primary.danger ?? false"
      :disabled="busy || Boolean(primary.disabled)"
      @click="select(primary.key)"
      >{{ primary.label }}</AButton
    >
    <ADropdown
      v-if="more.length"
      :trigger="['click']"
      placement="bottomRight"
      :disabled="busy"
      overlay-class-name="admin-row-actions-popup"
    >
      <AButton type="text" size="small" aria-label="更多操作" title="更多操作" :disabled="busy"
        ><EllipsisOutlined
      /></AButton>
      <template #overlay
        ><AMenu :selectable="false" @click="({ key }) => select(String(key))"
          ><AMenuItem
            v-for="item in more"
            :key="item.key"
            :danger="item.danger ?? false"
            :disabled="busy || Boolean(item.disabled)"
            >{{ item.label }}</AMenuItem
          ></AMenu
        ></template
      >
    </ADropdown>
  </div>
</template>
<style scoped>
.admin-row-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.admin-row-actions :deep(.ant-btn) {
  font-size: 13px;
}
.admin-row-actions :deep(.ant-btn-link) {
  padding: 0 4px;
}
.admin-row-actions :deep(.ant-btn-text) {
  width: 28px;
  padding: 0;
  color: var(--color-text-secondary);
}
</style>
<style>
.admin-row-actions-popup .ant-dropdown-menu {
  min-width: 140px;
  padding: 5px;
  border-radius: 9px;
}
.admin-row-actions-popup .ant-dropdown-menu-item {
  min-height: 34px;
  font-size: 13px;
}
</style>
