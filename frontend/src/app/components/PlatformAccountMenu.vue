<!-- Adapted from original PlatformAccountMenu.vue, MIT licensed. -->
<script setup lang="ts">
import {
  Avatar as AAvatar,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import { BgColorsOutlined, BulbOutlined, BellOutlined } from '@ant-design/icons-vue'
import { isDark, toggleTheme } from '../../shared/theme.ts'
import { identity } from '../../modules/identity/public.ts'
defineProps<{ leaving: boolean }>()
const emit = defineEmits<{ logout: [] }>()
</script>
<template>
  <div class="platform-account-menu is-compact" aria-label="账户与通知">
    <button
      class="platform-account-menu__icon"
      type="button"
      title="切换主题"
      aria-label="切换主题"
      @click="toggleTheme"
    >
      <BulbOutlined v-if="isDark" /><BgColorsOutlined v-else />
    </button>
    <button
      class="platform-account-menu__icon"
      disabled
      title="当前版本暂无通知服务"
      aria-label="通知"
    >
      <BellOutlined />
    </button>
    <ADropdown :trigger="['click']" placement="bottomRight"
      ><button
        type="button"
        class="platform-account-menu__profile"
        aria-label="用户菜单"
        :disabled="leaving"
      >
        <AAvatar :size="28">{{ identity?.username.slice(0, 1).toUpperCase() }}</AAvatar></button
      ><template #overlay
        ><AMenu
          ><AMenuItem disabled>{{ identity?.username }}</AMenuItem
          ><AMenuItem key="logout" @click="emit('logout')">退出登录</AMenuItem></AMenu
        ></template
      ></ADropdown
    >
  </div>
</template>
<style scoped lang="scss">
.platform-account-menu {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-top: 1px solid var(--gray-150);
}

.platform-account-menu__icon,
.platform-account-menu__profile {
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.platform-account-menu__icon {
  width: 32px;
  flex: 0 0 32px;
}

.platform-account-menu__profile {
  min-width: 0;
  flex: 1;
  gap: 8px;
  justify-content: flex-start;
  padding: 0 4px;
}

.platform-account-menu__profile :deep(.ant-avatar) {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
}

.platform-account-menu__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.platform-account-menu__icon:hover,
.platform-account-menu__profile:hover {
  background: var(--gray-50);
  color: var(--color-text);
}

.platform-account-menu__notice {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
}

.platform-account-menu.is-collapsed {
  flex-direction: column;
  padding: 8px 12px;
}

.platform-account-menu.is-collapsed .platform-account-menu__profile {
  width: 32px;
  flex: 0 0 32px;
  padding: 0 2px;
}

.platform-account-menu.is-compact {
  padding: 0;
  border-top: 0;
}

.platform-account-menu.is-compact .platform-account-menu__profile {
  width: 32px;
  flex: 0 0 32px;
  padding: 0 2px;
}
</style>
