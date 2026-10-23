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
        :can-open-ai="Boolean(aiWorkspacePath)"
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
        :can-open-ai="Boolean(aiWorkspacePath)"
        :show-account-menu="false"
        :show-collapse-control="false"
        @open-ai="openAi"
      />
    </a-drawer>

    <section class="platform-shell__main">
      <div v-if="!isMobile" class="platform-shell__desktop-topbar">
        <button
          v-if="mode === 'admin' && aiWorkspacePath"
          type="button"
          class="platform-shell__workspace-back"
          aria-label="返回 AI 工作台"
          @click="openAi"
        >
          <ArrowLeftOutlined />
          <span>返回 AI 工作台</span>
        </button>
        <PlatformAccountMenu class="platform-shell__desktop-account" compact @set-layout="setLayout" />
      </div>
      <PlatformTopbar
        v-if="isMobile"
        ref="topbarRef"
        :mode="mode"
        :can-open-ai="Boolean(aiWorkspacePath)"
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
import { ArrowLeftOutlined } from '@ant-design/icons-vue'
import {
  collectAuthorizedWorkspacePaths,
  collectWorkspaceLeafPaths,
  collectWorkspaceRoutePaths,
  filterAdminWorkspaceRoutes,
  resolveWorkspaceLandingPath,
  resolveWorkspaceMode,
} from '@/components/Workspace/workspaceTransforms.js'
import useAppStore from '@/store/modules/app'
import usePermissionStore from '@/store/modules/permission'
import useSettingsStore from '@/store/modules/settings'
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
const aiWorkspacePath = computed(() => {
  const paths = collectAuthorizedWorkspacePaths(permissionStore.addRoutes)
  const target = resolveWorkspaceLandingPath('/index', paths)
  return resolveWorkspaceMode(target) === 'ai' ? target : ''
})

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
  if (aiWorkspacePath.value) router.push(aiWorkspacePath.value)
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
.platform-shell__desktop-account { width: max-content; }
.platform-shell__workspace-back { display: inline-flex; align-items: center; gap: 8px; min-height: 32px; margin-right: auto; padding: 0 12px; border: 1px solid var(--gray-150); border-radius: var(--workspace-radius-sm); background: var(--bg-container); color: var(--color-text); font: inherit; cursor: pointer; }
.platform-shell__workspace-back:hover { border-color: var(--main-500); background: var(--main-30); color: var(--main-700); }
</style>

<style lang="scss">
.platform-mobile-drawer .ant-drawer-body { padding: 0; overflow: hidden; }
</style>
