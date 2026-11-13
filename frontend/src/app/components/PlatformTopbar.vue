<script setup lang="ts">
import { ref } from 'vue'
import { MenuOutlined } from '@ant-design/icons-vue'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
withDefaults(defineProps<{ title: string; leaving: boolean; mobile?: boolean }>(), { mobile: true })
const emit = defineEmits<{ 'toggle-sidebar': []; logout: [] }>()
const menuButtonRef = ref<HTMLButtonElement | null>(null)
defineExpose({ focusMenu: () => menuButtonRef.value?.focus() })
</script>
<template>
  <header class="platform-topbar">
    <button
      v-if="mobile"
      ref="menuButtonRef"
      type="button"
      class="platform-topbar__menu"
      aria-label="打开主菜单"
      @click="emit('toggle-sidebar')"
    >
      <MenuOutlined />
    </button>
    <div class="platform-topbar__context">
      <span class="sr-only">{{ title }}</span>
    </div>
    <div class="platform-topbar__actions">
      <PlatformAccountMenu :leaving="leaving" @logout="emit('logout')" />
    </div>
  </header>
</template>
<style scoped>
.platform-topbar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  height: var(--platform-topbar-height);
  min-height: var(--platform-topbar-height);
  padding: 0 32px;
  border-bottom: 0;
  background: var(--bg-page);
}
.platform-topbar__menu {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  color: var(--color-text-secondary);
  background: transparent;
  cursor: pointer;
}
.platform-topbar__context {
  flex: 1;
  min-width: 0;
}
.platform-topbar__actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 20px;
}
@media (max-width: 991px) {
  .platform-topbar {
    padding: 0 12px;
    gap: 10px;
  }
  .platform-topbar__actions {
    gap: 8px;
  }
}
</style>
