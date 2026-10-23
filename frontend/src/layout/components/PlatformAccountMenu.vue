<template>
  <div
    class="platform-account-menu"
    :class="{ 'is-collapsed': collapsed, 'is-compact': compact }"
    aria-label="账户与通知"
  >
    <button
      type="button"
      class="platform-account-menu__icon"
      aria-label="切换主题"
      title="切换主题"
      @click="settingsStore.toggleTheme()"
    >
      <BulbOutlined v-if="settingsStore.isDark" />
      <BgColorsOutlined v-else />
    </button>

    <HeaderNotice class="platform-account-menu__notice" />

    <a-dropdown :trigger="['click']" :placement="compact ? 'bottomRight' : 'topRight'">
      <button type="button" class="platform-account-menu__profile" aria-label="用户菜单">
        <a-avatar :size="28" :src="userStore.avatar" />
        <span v-if="!collapsed && !compact" class="platform-account-menu__name">
          {{ userStore.nickName }}
        </span>
      </button>
      <template #overlay>
        <a-menu @click="handleMenuClick">
          <a-menu-item key="profile">个人中心</a-menu-item>
          <a-menu-item v-if="settingsStore.showSettings" key="settings">界面设置</a-menu-item>
          <a-menu-item key="lock">锁定屏幕</a-menu-item>
          <a-menu-divider />
          <a-menu-item key="logout">退出登录</a-menu-item>
        </a-menu>
      </template>
    </a-dropdown>
  </div>
</template>

<script setup>
import { BgColorsOutlined, BulbOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import useLockStore from '@/store/modules/lock'
import useSettingsStore from '@/store/modules/settings'
import useUserStore from '@/store/modules/user'
import HeaderNotice from './HeaderNotice/index.vue'

defineProps({
  collapsed: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['set-layout'])
const route = useRoute()
const router = useRouter()
const lockStore = useLockStore()
const settingsStore = useSettingsStore()
const userStore = useUserStore()

function handleMenuClick({ key }) {
  if (key === 'profile') router.push('/user/profile')
  if (key === 'settings') emit('set-layout')
  if (key === 'lock') {
    lockStore.lockScreen(route.fullPath)
    router.push('/lock')
  }
  if (key === 'logout') logout()
}

async function logout() {
  try {
    await feedback.confirm('确定注销并退出系统吗？')
    await userStore.logOut()
    location.href = '/index'
  } catch {
    // Cancel keeps the current session.
  }
}
</script>

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
