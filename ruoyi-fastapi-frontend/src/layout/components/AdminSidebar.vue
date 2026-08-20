<template>
  <aside class="admin-sidebar sidebar-container" aria-label="管理控制台导航">
    <button type="button" class="admin-sidebar__back" @click="emit('open-ai')">
      <el-icon><ArrowLeft /></el-icon>
      <span v-if="!collapsed">返回 AI 工作台</span>
    </button>
    <el-scrollbar wrap-class="scrollbar-wrapper">
      <el-menu
        :default-active="activeMenu"
        :collapse="collapsed"
        :unique-opened="true"
        :collapse-transition="false"
        mode="vertical"
      >
        <SidebarItem
          v-for="(item, index) in adminRoutes"
          :key="`${item.path}-${index}`"
          :item="item"
          :base-path="item.path"
        />
      </el-menu>
    </el-scrollbar>
  </aside>
</template>

<script setup>
import { ArrowLeft } from '@element-plus/icons-vue'
import { filterAdminWorkspaceRoutes } from '@/components/Workspace/workspaceTransforms.js'
import usePermissionStore from '@/store/modules/permission'
import SidebarItem from './Sidebar/SidebarItem.vue'

defineProps({ collapsed: { type: Boolean, default: false } })
const emit = defineEmits(['open-ai'])
const route = useRoute()
const permissionStore = usePermissionStore()
const adminRoutes = computed(() => filterAdminWorkspaceRoutes(permissionStore.sidebarRouters))
const activeMenu = computed(() => route.meta.activeMenu || route.path)
</script>

<style scoped lang="scss">
.admin-sidebar { height: 100%; min-height: 0; display: flex; flex-direction: column; background: var(--workspace-surface); border-right: 1px solid var(--workspace-border); }
.admin-sidebar__back { min-height: 64px; display: flex; align-items: center; gap: 10px; padding: 0 18px; border: 0; border-bottom: 1px solid var(--workspace-border); background: transparent; color: var(--workspace-text); cursor: pointer; }
.admin-sidebar :deep(.el-scrollbar) { flex: 1; }
.admin-sidebar :deep(.el-menu) { width: 100%; border-right: 0; background: transparent; }
</style>
