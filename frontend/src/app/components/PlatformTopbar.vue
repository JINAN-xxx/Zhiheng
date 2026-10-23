<!-- Adapted from original PlatformTopbar.vue, MIT licensed. -->
<script setup lang="ts">
import { ref } from 'vue'
import { MenuOutlined } from '@ant-design/icons-vue'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
defineProps<{ title: string; leaving: boolean }>()
const emit = defineEmits<{ 'toggle-sidebar': []; logout: [] }>()
const menuButtonRef = ref<HTMLButtonElement | null>(null)
defineExpose({ focusMenu: () => menuButtonRef.value?.focus() })
</script>
<template>
  <header class="platform-topbar">
    <button
      ref="menuButtonRef"
      type="button"
      class="platform-topbar__menu"
      aria-label="打开主菜单"
      @click="emit('toggle-sidebar')"
    >
      <MenuOutlined />
    </button>
    <div class="platform-topbar__context">
      <strong>{{ title }}</strong>
    </div>
    <div class="platform-topbar__actions">
      <PlatformAccountMenu :leaving="leaving" @logout="emit('logout')" />
    </div>
  </header>
</template>
<style scoped lang="scss">
.platform-topbar {
  height: var(--platform-topbar-height);
  min-height: var(--platform-topbar-height);
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 12px;
  background: var(--bg-container);
  border-bottom: 1px solid var(--gray-150);
}
.platform-topbar button {
  font: inherit;
}
.platform-topbar__menu {
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.platform-topbar__menu:hover {
  border-color: var(--gray-150);
  background: var(--gray-50);
  color: var(--color-text);
}
.platform-topbar__context {
  min-width: 0;
  display: grid;
  gap: 1px;
}
.platform-topbar__context span {
  color: var(--color-text-tertiary);
  font-size: var(--workspace-font-size-caption);
}
.platform-topbar__context strong {
  display: block;
  overflow: hidden;
  color: var(--color-text);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.platform-topbar__actions {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.platform-topbar__mode {
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 9px;
  border: 1px solid var(--gray-150);
  border-radius: var(--workspace-radius-sm);
  background: var(--bg-container);
  color: var(--color-text-secondary);
  cursor: pointer;
}
.platform-topbar__mode:hover {
  background: var(--gray-50);
  color: var(--color-text);
}
@media (max-width: 760px) {
  .platform-topbar {
    padding: 0 8px;
  }
  .platform-topbar__context {
    flex: 1 1 auto;
  }
  .platform-topbar__actions {
    flex: 0 0 auto;
  }
  .platform-topbar__context span {
    display: none;
  }
  .platform-topbar.has-workspace-return .platform-topbar__context {
    display: none;
  }
  .platform-topbar__mode {
    flex: 0 0 auto;
    justify-content: center;
    white-space: nowrap;
  }
}
</style>
