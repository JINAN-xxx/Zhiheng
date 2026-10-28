<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { Alert as AAlert } from 'ant-design-vue'
import { identity } from '../../identity/public.ts'
import { getNavigation } from '../api/index.ts'
import type { NavigationEntry } from '../api/index.ts'
import { adminNavigation, hasAdminPermission, visibleAdminNavigation } from '../access.ts'
import { errorMessage, RequestScope, isAborted } from '../../../shared/http.ts'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
const menus = ref<NavigationEntry[]>(),
  navigationError = ref(''),
  ready = ref(false)
const route = useRoute(),
  router = useRouter(),
  scope = new RequestScope()
const navigation = computed(() => visibleAdminNavigation(identity.value, menus.value))
async function refreshNavigation(): Promise<void> {
  const ticket = scope.replace()
  try {
    const result = await getNavigation(ticket.signal)
    if (ticket.isCurrent()) {
      menus.value = result
      navigationError.value = ''
    }
  } catch (error) {
    if (ticket.isCurrent() && !isAborted(error)) navigationError.value = errorMessage(error)
  } finally {
    if (ticket.isCurrent()) ready.value = true
  }
}
provide('refreshAdminNavigation', refreshNavigation)
watch(
  [() => route.path, navigation, ready],
  () => {
    if (!ready.value) return
    if (route.path === '/admin' || route.path === '/admin/') {
      if (navigation.value[0]) void router.replace(navigation.value[0].path)
    }
  },
  { immediate: true }
)
const permitted = computed(() =>
  adminNavigation.some(
    (entry) => entry.path === route.path && hasAdminPermission(identity.value, entry.area)
  )
)
onMounted(refreshNavigation)
onBeforeUnmount(() => scope.dispose())
</script>
<template>
  <div class="admin-console">
    <aside class="admin-navigation" aria-label="后台管理导航">
      <div class="admin-navigation__title">后台管理</div>
      <nav>
        <RouterLink
          v-for="entry in navigation"
          :key="entry.path"
          :to="entry.path"
          :class="{ 'is-active': route.path === entry.path }"
          >{{ entry.label }}</RouterLink
        >
      </nav>
    </aside>
    <div class="admin-content">
      <AAlert
        v-if="navigationError"
        :message="'菜单配置加载失败：' + navigationError"
        type="warning"
        show-icon
      />
      <WorkspaceState v-if="!ready" kind="loading" />
      <RouterView v-else-if="permitted" :key="route.path" />
      <WorkspaceState
        v-else
        kind="empty"
        title="暂无可访问的管理页面"
        message="当前页面没有访问权限或已被隐藏。"
      />
    </div>
  </div>
</template>
<style lang="scss">
.admin-console {
  display: flex;
  min-width: 0;
  min-height: 100%;
  color: var(--color-text);
  background: var(--workspace-bg);
  font-family: var(--workspace-font-family);
}
.admin-navigation {
  flex: 0 0 164px;
  width: 164px;
  padding: 18px 12px;
  border-right: 1px solid var(--gray-150);
  background: transparent;
}
.admin-navigation__title {
  padding: 4px 12px 16px;
  font-size: 15px;
  font-weight: 600;
}
.admin-navigation nav {
  display: grid;
  gap: 4px;
}
.admin-navigation a {
  padding: 10px 12px;
  border-radius: 6px;
  color: var(--color-text-secondary);
  font-size: 13px;
  text-decoration: none;
}
.admin-navigation a:hover {
  color: var(--color-text);
  background: var(--gray-50);
}
.admin-navigation a.is-active {
  color: var(--main-700);
  background: var(--main-50);
  font-weight: 500;
}
.admin-content {
  flex: 1;
  min-width: 0;
}
.admin-page {
  min-width: 0;
}
.admin-page__body {
  display: grid;
  gap: 16px;
  padding: 20px 24px 28px;
}
.admin-page .workspace-toolbar {
  border-bottom: 0;
}
.admin-tools {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding-bottom: 18px;
}
.admin-tools .ant-input-search {
  width: min(320px, 100%);
}
.admin-total {
  margin-left: auto;
  color: var(--color-text-secondary);
  white-space: nowrap;
  font-size: 13px;
}
.admin-page .ant-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 20px;
}
.admin-actions {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: wrap;
}
.admin-actions .ant-btn {
  padding: 0 6px;
  font-size: 13px;
}
.admin-primary {
  display: grid;
  gap: 4px;
}
.admin-primary strong {
  font-weight: 500;
}
.admin-primary small,
.admin-muted {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.admin-code {
  font-family: var(--workspace-font-mono);
  overflow-wrap: anywhere;
}
.admin-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-width: 420px;
}
.admin-filter {
  min-height: 32px;
  padding: 3px 8px;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  color: var(--color-text);
  background: var(--bg-container);
}
@media (max-width: 991px) {
  .admin-console {
    flex-direction: column;
  }
  .admin-navigation {
    width: 100%;
    flex-basis: auto;
    box-sizing: border-box;
    border-right: 0;
    border-bottom: 1px solid var(--gray-150);
    padding: 10px 12px;
  }
  .admin-navigation__title {
    display: none;
  }
  .admin-navigation nav {
    display: flex;
    overflow-x: auto;
  }
  .admin-navigation a {
    white-space: nowrap;
  }
  .admin-page__body {
    padding: 14px;
  }
}
</style>
