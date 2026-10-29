<script setup lang="ts">
import {
  Avatar as AAvatar,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import { identity } from '../../modules/identity/public.ts'
import { canAccessAdmin } from '../../modules/admin/public.ts'
import { RouterLink } from 'vue-router'
import PlatformNotices from './PlatformNotices.vue'
withDefaults(
  defineProps<{ leaving: boolean; variant?: 'topbar' | 'sidebar'; collapsed?: boolean }>(),
  { variant: 'topbar', collapsed: false }
)
const emit = defineEmits<{ logout: [] }>()
</script>
<template>
  <div class="platform-account-menu" :class="[variant, { collapsed }]" aria-label="账户与通知">
    <template v-if="variant === 'topbar'">
      <PlatformNotices />
    </template>
    <ADropdown v-else :trigger="['click']" placement="topRight">
      <button
        type="button"
        class="platform-account-menu__profile"
        aria-label="用户菜单"
        :disabled="leaving"
      >
        <AAvatar :size="30">{{ identity?.username.slice(0, 1).toUpperCase() }}</AAvatar>
        <span v-if="!collapsed" class="platform-account-menu__identity"
          ><strong>{{ identity?.username }}</strong
          ><small>{{ identity?.is_admin ? '超级管理员' : '用户' }}</small></span
        >
        <DownOutlined v-if="!collapsed" class="platform-account-menu__chevron" />
      </button>
      <template #overlay
        ><AMenu
          ><AMenuItem disabled>{{ identity?.username }}</AMenuItem
          ><AMenuItem key="profile"><RouterLink to="/profile">个人资料</RouterLink></AMenuItem
          ><AMenuItem v-if="canAccessAdmin(identity)" key="admin"
            ><RouterLink to="/admin">后台管理</RouterLink></AMenuItem
          ><AMenuItem key="logout" @click="emit('logout')">退出登录</AMenuItem></AMenu
        ></template
      >
    </ADropdown>
  </div>
</template>
<style scoped>
.platform-account-menu {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.platform-account-menu__icon {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  width: 30px;
  height: 32px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  font-size: 18px;
  cursor: pointer;
}
.platform-account-menu__icon:hover {
  background: var(--gray-50);
  color: var(--main-color);
}
.platform-account-menu__icon:disabled {
  opacity: 0.45;
  cursor: default;
}
.platform-account-menu__profile {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 42px;
  padding: 2px 4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.platform-account-menu__profile:hover {
  background: var(--gray-50);
}
.platform-account-menu__profile :deep(.ant-avatar) {
  flex-shrink: 0;
  background: var(--main-50);
  color: var(--main-color);
}
.platform-account-menu__identity {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}
.platform-account-menu__identity strong {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 14px;
  font-weight: 500;
}
.platform-account-menu__identity small {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.platform-account-menu__chevron {
  color: var(--color-text-tertiary);
  font-size: 11px;
}
.sidebar {
  width: 100%;
}
.collapsed .platform-account-menu__profile {
  justify-content: center;
  padding: 0;
}
.platform-account-menu button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
