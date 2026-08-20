<template>
  <div
    class="platform-shell workspace-app-shell"
    :class="[`is-${mode}`, { 'is-collapsed': collapsed, 'is-mobile': isMobile }]"
    :style="{ '--current-color': theme, '--current-color-light': `${theme}1a` }"
  >
    <aside v-if="!isMobile" class="platform-shell__sidebar">
      <PlatformSidebar v-if="mode === 'ai'" :collapsed="collapsed" @open-admin="openAdmin" />
      <AdminSidebar v-else :collapsed="collapsed" @open-ai="openAi" />
    </aside>

    <el-drawer
      v-else
      v-model="appStore.mobileSidebarOpen"
      direction="ltr"
      size="min(86vw, 300px)"
      :show-close="false"
      :with-header="false"
      append-to-body
      class="platform-mobile-drawer"
    >
      <PlatformSidebar v-if="mode === 'ai'" @open-admin="openAdmin" />
      <AdminSidebar v-else @open-ai="openAi" />
    </el-drawer>

    <section class="platform-shell__main">
      <PlatformTopbar
        :mode="mode"
        @toggle-sidebar="toggleSidebar"
        @open-admin="openAdmin"
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
import { AdminSidebar, AppMain, PlatformSidebar, PlatformTopbar, Settings, TagsView } from './components'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const permissionStore = usePermissionStore()
const settingsStore = useSettingsStore()
const userStore = useUserStore()
const settingRef = ref(null)
const { width } = useWindowSize()

const mode = computed(() => resolveWorkspaceMode(route.path))
const isMobile = computed(() => width.value < 992)
const collapsed = computed(() => !appStore.sidebar.opened)
const needTagsView = computed(() => settingsStore.tagsView)
const theme = computed(() => settingsStore.theme)

watch(() => route.fullPath, () => appStore.closeMobileSidebar())

function toggleSidebar() {
  if (isMobile.value) appStore.openMobileSidebar()
  else appStore.toggleSideBar()
}

function openAdmin() {
  const routes = filterAdminWorkspaceRoutes(permissionStore.addRoutes)
  const target = collectWorkspaceRoutePaths(routes).find((path) => path !== '/index')
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
.platform-shell__sidebar { width: var(--workspace-sidebar-width); min-width: var(--workspace-sidebar-width); height: 100%; transition: width 0.2s ease, min-width 0.2s ease; }
.platform-shell.is-collapsed .platform-shell__sidebar { width: var(--workspace-sidebar-collapsed-width); min-width: var(--workspace-sidebar-collapsed-width); }
.platform-shell__main { min-width: 0; height: 100%; display: flex; flex: 1; flex-direction: column; overflow: hidden; }
</style>

<style lang="scss">
.platform-mobile-drawer .el-drawer__body { padding: 0; overflow: hidden; }
</style>
