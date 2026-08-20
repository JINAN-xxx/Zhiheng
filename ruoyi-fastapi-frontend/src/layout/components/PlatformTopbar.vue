<template>
  <header class="platform-topbar">
    <button ref="menuButtonRef" type="button" class="platform-topbar__menu" aria-label="打开主菜单" title="打开主菜单" @click="emit('toggle-sidebar')">
      <el-icon><Menu /></el-icon>
    </button>

    <div class="platform-topbar__context">
      <span>{{ mode === 'ai' ? 'AI 工作台' : '管理控制台' }}</span>
      <strong>{{ route.meta.title || (mode === 'ai' ? '智能工作空间' : '系统管理') }}</strong>
    </div>

    <div class="platform-topbar__actions">
      <button
        type="button"
        class="platform-topbar__mode"
        :title="mode === 'ai' ? '管理控制台' : '返回 AI 工作台'"
        @click="emit(mode === 'ai' ? 'open-admin' : 'open-ai')"
      >
        <el-icon><Grid v-if="mode === 'ai'" /><ChatDotRound v-else /></el-icon>
        <span>{{ mode === 'ai' ? '管理控制台' : '返回 AI 工作台' }}</span>
      </button>
      <button type="button" class="platform-topbar__icon" aria-label="切换主题" title="切换主题" @click="settingsStore.toggleTheme()">
        <el-icon><Sunny v-if="settingsStore.isDark" /><Moon v-else /></el-icon>
      </button>
      <HeaderNotice class="platform-topbar__notice" />

      <el-dropdown trigger="click" @command="handleCommand">
        <button type="button" class="platform-topbar__profile" aria-label="用户菜单">
          <img :src="userStore.avatar" alt="" />
          <span>{{ userStore.nickName }}</span>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="profile">个人中心</el-dropdown-item>
            <el-dropdown-item v-if="settingsStore.showSettings" command="settings">界面设置</el-dropdown-item>
            <el-dropdown-item command="lock">锁定屏幕</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup>
import { ChatDotRound, Grid, Menu, Moon, Sunny } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import useLockStore from '@/store/modules/lock'
import useSettingsStore from '@/store/modules/settings'
import useUserStore from '@/store/modules/user'
import HeaderNotice from './HeaderNotice/index.vue'

defineProps({ mode: { type: String, required: true } })
const emit = defineEmits(['open-admin', 'open-ai', 'set-layout', 'toggle-sidebar'])
const route = useRoute()
const router = useRouter()
const lockStore = useLockStore()
const settingsStore = useSettingsStore()
const userStore = useUserStore()
const menuButtonRef = ref(null)

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

async function logout() {
  try {
    await ElMessageBox.confirm('确定注销并退出系统吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await userStore.logOut()
    location.href = '/index'
  } catch {
    // Cancel keeps the current session.
  }
}

defineExpose({ focusMenu })
</script>

<style scoped lang="scss">
.platform-topbar { height: var(--platform-topbar-height); min-height: var(--platform-topbar-height); display: flex; align-items: center; gap: 12px; padding: 0 12px; background: var(--workspace-surface); border-bottom: 1px solid var(--workspace-border); }
.platform-topbar button { font: inherit; }
.platform-topbar__menu,
.platform-topbar__icon { display: inline-flex; width: 32px; height: 32px; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--workspace-text-secondary); cursor: pointer; }
.platform-topbar__menu:hover,
.platform-topbar__icon:hover { border-color: var(--workspace-border); background: var(--workspace-surface-muted); color: var(--workspace-text); }
.platform-topbar__context { min-width: 0; display: grid; gap: 1px; }
.platform-topbar__context span { color: var(--workspace-text-muted); font-size: 11px; }
.platform-topbar__context strong { overflow: hidden; color: var(--workspace-text); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.platform-topbar__actions { min-width: 0; display: flex; align-items: center; gap: 6px; margin-left: auto; }
.platform-topbar__mode,
.platform-topbar__profile { min-height: 32px; display: inline-flex; align-items: center; gap: 8px; padding: 0 9px; border: 1px solid var(--workspace-border); border-radius: var(--workspace-radius-sm); background: var(--workspace-surface); color: var(--workspace-text-secondary); cursor: pointer; }
.platform-topbar__profile img { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; }
.platform-topbar__notice { height: 32px; display: flex; align-items: center; }
@media (max-width: 760px) {
  .platform-topbar { padding: 0 8px; }
  .platform-topbar__context span,
  .platform-topbar__mode span,
  .platform-topbar__profile span { display: none; }
  .platform-topbar__mode { width: 32px; justify-content: center; padding: 0; }
}
</style>
