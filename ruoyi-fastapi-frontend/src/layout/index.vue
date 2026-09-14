<template>
  <div
    class="platform-shell workspace-app-shell"
    :class="[`is-${mode}`, { 'is-collapsed': collapsed, 'is-mobile': isMobile }]"
    :style="{ '--current-color': theme, '--current-color-light': `${theme}1a` }"
  >
    <aside v-if="!isMobile" class="platform-shell__sidebar">
      <PlatformSidebar
        v-if="mode === 'ai'"
        :collapsed="collapsed"
        show-collapse-control
        @open-admin="openAdmin"
        @set-layout="setLayout"
        @toggle-collapse="toggleSidebar"
      />
      <AdminSidebar
        v-else
        :collapsed="collapsed"
        show-collapse-control
        @open-ai="openAi"
        @set-layout="setLayout"
        @toggle-collapse="toggleSidebar"
      />
    </aside>

    <a-drawer
      v-else
      :open="appStore.mobileSidebarOpen"
      placement="left"
      :width="mobileDrawerWidth"
      :closable="false"
      :autofocus="false"
      root-class-name="platform-mobile-drawer"
      @update:open="updateMobileSidebar"
      @close="appStore.closeMobileSidebar"
      @after-open-change="handleMobileDrawerChange"
    >
      <PlatformSidebar
        v-if="mode === 'ai'"
        ref="mobileSidebarRef"
        :show-account-menu="false"
        :show-collapse-control="false"
        @open-admin="openAdmin"
      />
      <AdminSidebar
        v-else
        ref="mobileSidebarRef"
        :show-account-menu="false"
        :show-collapse-control="false"
        @open-ai="openAi"
      />
    </a-drawer>

    <section class="platform-shell__main">
      <div v-if="!isMobile" class="platform-shell__desktop-topbar">
        <strong v-if="route.path === '/ai/work'" class="platform-shell__work-title">{{ workTaskStore.currentTask?.title || '工作' }}</strong>
        <PlatformAccountMenu class="platform-shell__desktop-account" compact @set-layout="setLayout" />
      </div>
      <PlatformTopbar
        v-if="isMobile"
        ref="topbarRef"
        :mode="mode"
        @toggle-sidebar="toggleSidebar"
        @open-ai="openAi"
        @set-layout="setLayout"
      />
      <TagsView v-if="mode === 'admin' && needTagsView" />
      <AppMain />
    </section>
    <Settings ref="settingRef" />
  </div>
</template>

<script setup>
import { useWindowSize } from '@vueuse/core'
import {
  collectWorkspaceLeafPaths,
  collectWorkspaceRoutePaths,
  filterAdminWorkspaceRoutes,
  resolveAiResourceTabs,
  resolveWorkspaceLandingPath,
  resolveWorkspaceMode,
} from '@/components/Workspace/workspaceTransforms.js'
import useAppStore from '@/store/modules/app'
import usePermissionStore from '@/store/modules/permission'
import useSettingsStore from '@/store/modules/settings'
import useUserStore from '@/store/modules/user'
import useWorkTaskStore from '@/store/modules/workTask'
import {
  AdminSidebar,
  AppMain,
  PlatformAccountMenu,
  PlatformSidebar,
  PlatformTopbar,
  Settings,
  TagsView,
} from './components'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const permissionStore = usePermissionStore()
const settingsStore = useSettingsStore()
const userStore = useUserStore()
const workTaskStore = useWorkTaskStore()
const settingRef = ref(null)
const topbarRef = ref(null)
const mobileSidebarRef = ref(null)
const { width } = useWindowSize()

const mode = computed(() => resolveWorkspaceMode(route.path))
const isMobile = computed(() => width.value < 992)
const mobileDrawerWidth = computed(() => Math.min(width.value * 0.86, 300))
const collapsed = computed(() => !appStore.sidebar.opened)
const needTagsView = computed(() => settingsStore.tagsView)
const theme = computed(() => settingsStore.theme)

watch(() => route.fullPath, () => appStore.closeMobileSidebar())

function toggleSidebar() {
  if (isMobile.value) appStore.openMobileSidebar()
  else appStore.toggleSideBar()
}

async function focusMobileSidebar() {
  await nextTick()
  const root = mobileSidebarRef.value?.$el
  root?.querySelector('button, a[href], input')?.focus()
}

function restoreMenuFocus() {
  topbarRef.value?.focusMenu()
}

function updateMobileSidebar(open) {
  if (open) appStore.openMobileSidebar()
  else appStore.closeMobileSidebar()
}

function handleMobileDrawerChange(open) {
  if (open) focusMobileSidebar()
  else restoreMenuFocus()
}

function openAdmin() {
  const routes = filterAdminWorkspaceRoutes(permissionStore.addRoutes)
  const target = collectWorkspaceLeafPaths(routes)[0]
    || collectWorkspaceRoutePaths(routes).find((path) => path !== '/index')
  if (target) router.push(target)
}

function openAi() {
  const accessiblePaths = collectWorkspaceRoutePaths(permissionStore.addRoutes)
  const target = resolveWorkspaceLandingPath('/index', accessiblePaths)
  if (resolveWorkspaceMode(target) === 'ai') router.push(target)
  else {
    const firstResource = resolveAiResourceTabs(new Set(userStore.permissions))[0]
    if (firstResource) router.push(firstResource.path)
  }
}

function setLayout() {
  settingRef.value?.openSetting()
}
</script>

<style scoped lang="scss">
.platform-shell { width: 100%; height: 100vh; display: flex; overflow: hidden; background: var(--workspace-bg); }
.platform-shell__sidebar { width: var(--platform-sidebar-width); min-width: var(--platform-sidebar-width); flex: 0 0 var(--platform-sidebar-width); height: 100%; margin: 0; padding: 0; border-radius: 0; background: transparent; line-height: normal; transition: width 0.2s ease, min-width 0.2s ease, flex-basis 0.2s ease; }
.platform-shell.is-collapsed .platform-shell__sidebar { width: var(--platform-sidebar-collapsed-width); min-width: var(--platform-sidebar-collapsed-width); flex-basis: var(--platform-sidebar-collapsed-width); }
.platform-shell__main { min-width: 0; height: 100%; display: flex; flex: 1; flex-direction: column; overflow: hidden; }
.platform-shell__desktop-topbar { height: var(--platform-topbar-height); min-height: var(--platform-topbar-height); display: flex; align-items: center; justify-content: flex-end; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--gray-150); background: var(--bg-container); }
.platform-shell__work-title { min-width: 0; margin-right: auto; overflow: hidden; color: var(--color-text); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.platform-shell__desktop-account { width: max-content; }
</style>

<style lang="scss">
.platform-mobile-drawer .ant-drawer-body { padding: 0; overflow: hidden; }
</style>
