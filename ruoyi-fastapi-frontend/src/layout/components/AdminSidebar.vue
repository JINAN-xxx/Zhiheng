<template>
  <aside class="admin-sidebar" :class="{ 'is-collapsed': collapsed }" aria-label="管理控制台导航">
    <div class="admin-sidebar__header">
      <router-link class="admin-sidebar__brand" to="/ai/chat" aria-label="返回智能工作台">
        <img :src="logo" alt="智衡平台" class="admin-sidebar__brand-logo" />
      </router-link>
      <button
        v-if="showCollapseControl"
        type="button"
        class="admin-sidebar__collapse"
        :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'"
        :title="collapsed ? '展开侧边栏' : '折叠侧边栏'"
        @click="emit('toggle-collapse')"
      >
        <MenuUnfoldOutlined v-if="collapsed" />
        <MenuFoldOutlined v-else />
      </button>
    </div>
    <div class="admin-sidebar__scroll">
      <a-menu
        v-model:open-keys="openKeys"
        :selected-keys="[activeMenu]"
        :inline-collapsed="collapsed"
        mode="inline"
        @open-change="handleOpenChange"
      >
        <SidebarItem
          v-for="(item, index) in adminRoutes"
          :key="`${item.path}-${index}`"
          :item="item"
          :base-path="item.path"
        />
      </a-menu>
    </div>
    <PlatformAccountMenu
      v-if="showAccountMenu"
      :collapsed="collapsed"
      @set-layout="emit('set-layout')"
    />
  </aside>
</template>

<script setup>
import { ArrowLeftOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons-vue'
import logo from '@/assets/logo/logo.png'
import { filterAdminWorkspaceRoutes } from '@/components/Workspace/workspaceTransforms.js'
import usePermissionStore from '@/store/modules/permission'
import { getNormalPath } from '@/utils/ruoyi'
import { isExternal } from '@/utils/validate'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
import SidebarItem from './Sidebar/SidebarItem.vue'

defineProps({
  collapsed: { type: Boolean, default: false },
  showAccountMenu: { type: Boolean, default: false },
  showCollapseControl: { type: Boolean, default: false },
})
const emit = defineEmits(['open-ai', 'set-layout', 'toggle-collapse'])
const route = useRoute()
const permissionStore = usePermissionStore()
const adminRoutes = computed(() => filterAdminWorkspaceRoutes(permissionStore.sidebarRouters))
const activeMenu = computed(() => route.meta.activeMenu || route.path)
const openKeys = ref([])

function resolveMenuPath(basePath, routePath) {
  if (isExternal(routePath)) return routePath
  if (isExternal(basePath)) return basePath
  return getNormalPath(`${basePath}/${routePath}`)
}

function findMenuKeyChain(routes, target, basePath = '', parents = []) {
  for (const item of routes || []) {
    if (item.hidden) continue
    const currentPath = resolveMenuPath(basePath, item.path)
    const chain = [...parents, currentPath]
    if (currentPath === target) return chain
    const nested = findMenuKeyChain(item.children, target, currentPath, chain)
    if (nested.length) return nested
  }
  return []
}

function syncOpenKeys() {
  const chain = findMenuKeyChain(adminRoutes.value, activeMenu.value)
  openKeys.value = chain.slice(0, -1)
}

function handleOpenChange(keys) {
  const latestKey = keys.find((key) => !openKeys.value.includes(key))
  if (!latestKey) {
    openKeys.value = keys
    return
  }
  openKeys.value = findMenuKeyChain(adminRoutes.value, latestKey)
}

watch([adminRoutes, activeMenu], syncOpenKeys, { immediate: true })
</script>

<style scoped lang="scss">
.admin-sidebar { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; margin: 0; padding: 0; border-radius: 0; background: var(--bg-sider); border-right: 1px solid var(--gray-150); color: var(--color-text); font: inherit; line-height: normal; }
.admin-sidebar__header { min-height: 56px; display: flex; align-items: center; gap: 4px; padding: 0 10px; border-bottom: 1px solid var(--gray-150); }
.admin-sidebar__brand { min-width: 0; display: flex; flex: 1; align-items: center; gap: 8px; text-decoration: none; }
.admin-sidebar__brand-logo { height: 40px; width: auto; object-fit: contain; }
.admin-sidebar__brand-text { font-size: 14px; font-weight: 700; white-space: nowrap; color: var(--color-text); font-family: Helvetica Neue, Helvetica, PingFang SC, Hiragino Sans GB, Microsoft YaHei, Arial, sans-serif; }
.admin-sidebar__back { min-width: 0; min-height: 32px; display: flex; flex: 1; align-items: center; gap: 10px; padding: 0 4px; overflow: hidden; border: 0; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text); white-space: nowrap; cursor: pointer; }
.admin-sidebar__collapse { width: 30px; height: 30px; display: inline-flex; flex: 0 0 30px; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.admin-sidebar__back:hover,
.admin-sidebar__collapse:hover { background: var(--gray-50); color: var(--color-text); }
.admin-sidebar__scroll { min-height: 0; flex: 1; overflow-y: auto; padding: 6px 0; }
.admin-sidebar :deep(.ant-menu) { width: 100%; border-inline-end: 0; background: transparent; }
.admin-sidebar :deep(.ant-menu-item),
.admin-sidebar :deep(.ant-menu-submenu-title) { height: var(--platform-menu-item-height); line-height: var(--platform-menu-item-height); margin: 3px 8px; border-radius: var(--workspace-radius-sm); color: var(--color-text-secondary); }
.admin-sidebar :deep(.ant-menu-item:hover),
.admin-sidebar :deep(.ant-menu-submenu-title:hover) { color: var(--main-700); background: var(--main-30); }
.admin-sidebar :deep(.ant-menu-item-selected),
.admin-sidebar :deep(.ant-menu-submenu-selected > .ant-menu-submenu-title) { color: var(--main-700); background: var(--main-50); }
.admin-sidebar.is-collapsed :deep(.ant-menu-item),
.admin-sidebar.is-collapsed :deep(.ant-menu-submenu-title) { display: flex; align-items: center; justify-content: center; padding-inline: 0 !important; }
.admin-sidebar.is-collapsed :deep(.sidebar-menu-link) { width: 100%; justify-content: center; gap: 0; }
.admin-sidebar.is-collapsed :deep(.menu-title),
.admin-sidebar.is-collapsed :deep(.ant-menu-title-content) { display: none; }
.admin-sidebar.is-collapsed :deep(.svg-icon) { width: 16px; height: 16px; flex: 0 0 16px; margin: 0; }
.admin-sidebar.is-collapsed .admin-sidebar__header { gap: 0; padding-inline: 0; }
.admin-sidebar.is-collapsed .admin-sidebar__brand { justify-content: center; width: 26px; flex: 0 0 26px; }
.admin-sidebar.is-collapsed .admin-sidebar__brand-logo { height: 28px; width: auto; }
.admin-sidebar.is-collapsed .admin-sidebar__back { width: 26px; flex: 0 0 26px; justify-content: center; padding: 0; }
</style>