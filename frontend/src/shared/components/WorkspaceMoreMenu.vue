<!-- Adapted from the original platform UI, MIT licensed. -->
<template>
  <a-dropdown
    :trigger="['click']"
    placement="bottomRight"
    overlay-class-name="workspace-more-popup"
    :get-popup-container="popupContainer"
  >
    <button type="button" class="workspace-more-menu" aria-label="更多操作" title="更多操作">
      <EllipsisOutlined />
    </button>
    <template #overlay>
      <a-menu :selectable="false" @click="handleMenuClick">
        <a-menu-item
          v-for="(item, index) in items"
          :key="item.command"
          :danger="item.danger ?? false"
          :class="{ 'workspace-more-popup__divided': item.divided && index > 0 }"
        >
          <span class="workspace-more-popup__item">
            <span class="workspace-more-popup__icon" aria-hidden="true">
              <component :is="item.icon" v-if="item.icon" />
            </span>
            <span class="workspace-more-popup__label">{{ item.label }}</span>
          </span>
        </a-menu-item>
      </a-menu>
    </template>
  </a-dropdown>
</template>

<script setup lang="ts">
import { Dropdown as ADropdown, Menu as AMenu, MenuItem as AMenuItem } from 'ant-design-vue'
import { EllipsisOutlined } from '@ant-design/icons-vue'
import type { Component } from 'vue'
export interface MenuAction {
  command: string
  label: string
  icon?: Component
  danger?: boolean
  divided?: boolean
}
const props = withDefaults(defineProps<{ items?: MenuAction[] }>(), { items: () => [] })
const emit = defineEmits<{ command: [command: string, item: MenuAction] }>()
function popupContainer(triggerNode: HTMLElement): HTMLElement {
  return triggerNode.ownerDocument.body
}
function handleMenuClick({ key }: { key: string | number }): void {
  const item = props.items.find((entry) => entry.command === key)
  if (item) emit('command', item.command, item)
}
</script>

<style scoped lang="scss">
.workspace-more-menu {
  display: inline-flex;
  width: 30px;
  height: 30px;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--workspace-text-secondary);
  cursor: pointer;

  &:hover {
    border-color: var(--workspace-border);
    background: var(--workspace-surface-muted);
    color: var(--workspace-main-deep);
  }
}

// The popup is mounted on body, outside the trigger's scoped DOM tree.
</style>

<style lang="scss">
.workspace-more-popup {
  max-width: calc(100vw - 24px);

  .ant-dropdown-menu {
    min-width: 176px;
    padding: 6px;
    border: 1px solid var(--workspace-border);
    border-radius: 10px;
    background: var(--bg-elevated);
    box-shadow: var(--workspace-shadow-float);
  }

  .ant-dropdown-menu-item {
    min-height: 38px;
    padding: 8px 10px;
    border-radius: 6px;
    font-size: 14px;
    line-height: 22px;
  }

  .ant-dropdown-menu-item.workspace-more-popup__divided {
    position: relative;
    margin-top: 9px;

    &::before {
      position: absolute;
      top: -5px;
      right: 0;
      left: 0;
      border-top: 1px solid var(--workspace-border);
      content: '';
      pointer-events: none;
    }
  }

  &__item {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  &__icon {
    display: inline-flex;
    flex: 0 0 16px;
    width: 16px;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    line-height: 1;
  }

  &__label {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
  }
}
</style>
