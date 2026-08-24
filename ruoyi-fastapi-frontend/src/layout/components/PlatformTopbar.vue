<template>
  <header class="platform-topbar">
    <button ref="menuButtonRef" type="button" class="platform-topbar__menu" aria-label="打开主菜单" title="打开主菜单" @click="emit('toggle-sidebar')">
      <MenuOutlined />
    </button>

    <div class="platform-topbar__context">
      <strong>{{ currentTitle }}</strong>
    </div>

    <div class="platform-topbar__actions">
      <button
        v-if="mode !== 'ai'"
        type="button"
        class="platform-topbar__mode"
        title="返回智能工作台"
        @click="emit('open-ai')"
      >
        <MessageOutlined />
        <span>返回智能工作台</span>
      </button>
      <button type="button" class="platform-topbar__icon" aria-label="切换主题" title="切换主题" @click="settingsStore.toggleTheme()">
        <BulbOutlined v-if="settingsStore.isDark" />
        <BgColorsOutlined v-else />
      </button>
      <HeaderNotice class="platform-topbar__notice" />

      <a-dropdown :trigger="['click']">
        <button type="button" class="platform-topbar__profile" aria-label="用户菜单">
          <a-avatar :size="28" :src="userStore.avatar" />
          <span class="platform-topbar__profile-name">{{ userStore.nickName }}</span>
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
  </header>
</template>

<script setup>
import { BgColorsOutlined, BulbOutlined, MenuOutlined, MessageOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import useLockStore from '@/store/modules/lock'
import useSettingsStore from '@/store/modules/settings'
import useUserStore from '@/store/modules/user'
import HeaderNotice from './HeaderNotice/index.vue'

const props = defineProps({ mode: { type: String, required: true } })
const emit = defineEmits(['open-admin', 'open-ai', 'set-layout', 'toggle-sidebar'])
const route = useRoute()
const router = useRouter()
const lockStore = useLockStore()
const settingsStore = useSettingsStore()
const userStore = useUserStore()
const menuButtonRef = ref(null)
const aiTitles = Object.freeze({
  '/ai/chat': '智能对话',
  '/ai/model': '模型中心',
  '/ai/application': '开发接入',
  '/ai/usage': '用量记录',
})
const currentTitle = computed(() => (
  props.mode === 'ai' ? aiTitles[route.path] || '智能工作台' : route.meta.title || '管理后台'
))

function focusMenu() {
  menuButtonRef.value?.focus()
}

function handleCommand(command) {
  if (command === 'profile') router.push('/user/profile')
  if (command === 'settings') emit('set-layout')
  if (command === 'lock') {
    lockStore.lockScreen(route.fullPath)
    router.push('/lock')
  }
  if (command === 'logout') logout()
}

function handleMenuClick({ key }) {
  handleCommand(key)
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

defineExpose({ focusMenu })
</script>

<style scoped lang="scss">
.platform-topbar { height: var(--platform-topbar-height); min-height: var(--platform-topbar-height); display: flex; align-items: center; gap: 12px; padding: 0 12px; background: var(--gray-0); border-bottom: 1px solid var(--gray-150); }
.platform-topbar button { font: inherit; }
.platform-topbar__menu,
.platform-topbar__icon { display: inline-flex; width: 32px; height: 32px; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.platform-topbar__menu:hover,
.platform-topbar__icon:hover { border-color: var(--gray-150); background: var(--gray-50); color: var(--color-text); }
.platform-topbar__context { min-width: 0; display: grid; gap: 1px; }
.platform-topbar__context span { color: var(--color-text-tertiary); font-size: 11px; }
.platform-topbar__context strong { overflow: hidden; color: var(--color-text); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.platform-topbar__actions { min-width: 0; display: flex; align-items: center; gap: 6px; margin-left: auto; }
.platform-topbar__mode,
.platform-topbar__profile { min-height: 32px; display: inline-flex; align-items: center; gap: 8px; padding: 0 9px; border: 1px solid var(--gray-150); border-radius: var(--workspace-radius-sm); background: var(--gray-0); color: var(--color-text-secondary); cursor: pointer; }
.platform-topbar__mode:hover,
.platform-topbar__profile:hover { background: var(--gray-50); color: var(--color-text); }
.platform-topbar__notice { height: 32px; display: flex; align-items: center; }
@media (max-width: 760px) {
  .platform-topbar { padding: 0 8px; }
  .platform-topbar__context { flex: 1 1 auto; }
  .platform-topbar__actions { flex: 0 0 auto; }
  .platform-topbar__context span,
  .platform-topbar__mode span,
  .platform-topbar__profile-name { display: none; }
  .platform-topbar__mode,
  .platform-topbar__profile { width: 32px; min-width: 32px; flex: 0 0 32px; justify-content: center; padding: 0; overflow: visible; }
  .platform-topbar__profile :deep(.ant-avatar) { flex: 0 0 28px; }
}
</style>
