<template>
  <el-dropdown trigger="click" @command="handleCommand">
    <button type="button" class="workspace-more-menu" aria-label="更多操作" title="更多操作">
      <el-icon><MoreFilled /></el-icon>
    </button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item v-for="item in items" :key="item.command" :command="item.command" :divided="item.divided">
          <el-icon v-if="item.icon"><component :is="item.icon" /></el-icon>
          {{ item.label }}
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup>
import { MoreFilled } from '@element-plus/icons-vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
})

const emit = defineEmits(['command'])

function handleCommand(command) {
  const item = props.items.find((entry) => entry.command === command)
  emit('command', command, item)
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
</style>
