<template>
  <a-dropdown :trigger="['click']">
    <button type="button" class="workspace-more-menu" aria-label="更多操作" title="更多操作">
      <EllipsisOutlined />
    </button>
    <template #overlay>
      <a-menu @click="handleMenuClick">
        <a-menu-item
          v-for="item in items"
          :key="item.command"
          :class="{ 'is-divided': item.divided }"
          :danger="item.danger"
        >
          <component :is="item.icon" v-if="item.icon" />
          {{ item.label }}
        </a-menu-item>
      </a-menu>
    </template>
  </a-dropdown>
</template>

<script setup>
import { EllipsisOutlined } from '@ant-design/icons-vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
})

const emit = defineEmits(['command'])

function handleCommand(command) {
  const item = props.items.find((entry) => entry.command === command)
  emit('command', command, item)
}

function handleMenuClick({ key }) {
  handleCommand(key)
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

:deep(.ant-dropdown-menu-item.is-divided) {
  margin-top: 5px;
  border-top: 1px solid var(--workspace-border);
  border-radius: 0 0 var(--workspace-radius-sm) var(--workspace-radius-sm);
}
</style>
