<template>
  <aside class="admin-sidebar" :class="{ 'is-collapsed': collapsed }" aria-label="管理控制台导航">
    <button v-if="!collapsed" type="button" class="admin-sidebar__back" aria-label="返回 AI 工作台" title="返回 AI 工作台" @click="emit('open-ai')">
      <ArrowLeftOutlined />
      <span v-if="!collapsed">返回 AI 工作台</span>
    </button>
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
  </aside>
</template>

<script setup>
import { ArrowLeftOutlined } from '@ant-design/icons-vue'
import { filterAdminWorkspaceRoutes } from '@/components/Workspace/workspaceTransforms.js'
import usePermissionStore from '@/store/modules/permission'
import { getNormalPath } from '@/utils/ruoyi'
import { isExternal } from '@/utils/validate'
import SidebarItem from './Sidebar/SidebarItem.vue'

defineProps({ collapsed: { type: Boolean, default: false } })
const emit = defineEmits(['open-ai'])
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
.admin-sidebar { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; margin: 0; padding: 0; border-radius: 0; background: var(--workspace-surface); border-right: 1px solid var(--workspace-border); color: var(--workspace-text); font: inherit; line-height: normal; }
.admin-sidebar__back { width: 100%; min-height: var(--platform-topbar-height); display: flex; align-items: center; gap: 10px; padding: 0 14px; overflow: hidden; border: 0; border-bottom: 1px solid var(--workspace-border); background: transparent; color: var(--workspace-text); white-space: nowrap; cursor: pointer; }
.admin-sidebar__scroll { min-height: 0; flex: 1; overflow-y: auto; padding: 6px 0; }
.admin-sidebar :deep(.ant-menu) { width: 100%; border-inline-end: 0; background: transparent; }
.admin-sidebar :deep(.ant-menu-item),
.admin-sidebar :deep(.ant-menu-submenu-title) { height: var(--platform-menu-item-height); line-height: var(--platform-menu-item-height); margin: 3px 8px; border-radius: var(--workspace-radius-sm); }
.admin-sidebar :deep(.ant-menu-item-selected),
.admin-sidebar :deep(.ant-menu-submenu-selected > .ant-menu-submenu-title) { color: var(--workspace-main-deep); background: var(--workspace-main-soft); }
</style>
