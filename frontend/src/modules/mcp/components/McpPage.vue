<script setup lang="ts">
import { TabPane as ATabPane, Tabs as ATabs } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { hasPermission } from '../../../shared/platformAccess.ts'
import { identity } from '../../identity/public.ts'
import { MCP_ADMIN_PERMISSIONS } from '../permissions.ts'

defineProps<{ active: 'catalog' | 'servers' }>()
const router = useRouter()

function changeTab(key: string | number) {
  if (key === 'catalog') void router.push({ name: 'mcp-catalog' })
  if (key === 'servers') void router.push({ name: 'mcp-servers' })
}
</script>

<template>
  <main class="app-container mcp-page">
    <WorkspaceToolbar page title="MCP">
      <template #actions><slot name="actions" /></template>
    </WorkspaceToolbar>
    <section class="mcp-content">
      <ATabs :active-key="active" aria-label="MCP 分类" @change="changeTab">
        <ATabPane key="catalog" tab="工具目录" />
        <ATabPane
          v-if="MCP_ADMIN_PERMISSIONS.some((code) => hasPermission(identity, code))"
          key="servers"
          tab="服务管理"
        />
      </ATabs>
      <slot />
    </section>
  </main>
</template>

<style scoped>
.mcp-page {
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
.mcp-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 0 28px 28px;
}
.mcp-content :deep(.ant-tabs-nav) {
  margin: 0 0 22px;
}
@media (max-width: 767px) {
  .mcp-content {
    padding: 0 14px 16px;
  }
}
</style>
