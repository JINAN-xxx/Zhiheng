<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  AppstoreOutlined,
  ApiOutlined,
  BarChartOutlined,
  DatabaseOutlined,
  HomeOutlined,
  MessageOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  TeamOutlined,
  RightOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined
} from '@ant-design/icons-vue'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
import BrandMark from '../../shared/components/BrandMark.vue'
import { identity } from '../../modules/identity/public.ts'
const props = withDefaults(
  defineProps<{ collapsed: boolean; showCollapseControl?: boolean; leaving?: boolean }>(),
  { leaving: false }
)
const emit = defineEmits<{ 'toggle-collapse': []; navigate: []; logout: [] }>()
const route = useRoute()
const navigationId = useId()
const groups = [
  {
    id: 'applications',
    title: '应用',
    items: [
      { path: '/chat', label: '对话体验', icon: MessageOutlined },
      { path: '/agents', label: '智能体', icon: RobotOutlined },
      { path: '/work', label: '工作空间', icon: TeamOutlined }
    ]
  },
  {
    id: 'resources',
    title: '资源',
    items: [
      { path: '/models', label: '模型目录', icon: AppstoreOutlined },
      { path: '/knowledge', label: '知识库', icon: DatabaseOutlined },
      { path: '/skills', label: '技能管理', icon: ThunderboltOutlined }
    ]
  },
  {
    id: 'development',
    title: '开发与用量',
    items: [
      { path: '/developer', label: '开发接入', icon: ApiOutlined },
      { path: '/usage', label: '用量与调用', icon: BarChartOutlined }
    ]
  }
]
type NavigationGroup = (typeof groups)[number]
const groupIds = new Set(groups.map((group) => group.id))
const closedGroups = ref<string[]>([])
const preferenceKey = computed(() =>
  identity.value ? `zhiheng:primary-navigation:${encodeURIComponent(identity.value.id)}` : null
)
watch(
  preferenceKey,
  (key) => {
    closedGroups.value = []
    if (!key) return
    try {
      const stored: unknown = JSON.parse(window.localStorage.getItem(key) ?? '[]')
      if (Array.isArray(stored))
        closedGroups.value = [
          ...new Set(
            stored.filter((id): id is string => typeof id === 'string' && groupIds.has(id))
          )
        ]
    } catch {
      // 浏览器禁止存储或旧偏好无效时，默认展开，保留导航能力。
    }
  },
  { immediate: true, flush: 'sync' }
)
function active(path: string): boolean {
  return route.path === path || route.path.startsWith(path + '/') || route.meta.activeMenu === path
}
function isGroupActive(group: NavigationGroup): boolean {
  return group.items.some((item) => active(item.path))
}
function isGroupExpanded(group: NavigationGroup): boolean {
  return props.collapsed || isGroupActive(group) || !closedGroups.value.includes(group.id)
}
function toggleGroup(group: NavigationGroup): void {
  if (isGroupActive(group)) return
  closedGroups.value = closedGroups.value.includes(group.id)
    ? closedGroups.value.filter((id) => id !== group.id)
    : [...closedGroups.value, group.id]
  if (!preferenceKey.value) return
  try {
    window.localStorage.setItem(preferenceKey.value, JSON.stringify(closedGroups.value))
  } catch {
    // 偏好写入失败不影响本次展开、收起操作。
  }
}
</script>
<template>
  <aside class="platform-sidebar" :class="{ collapsed }">
    <div class="platform-brand-row">
      <RouterLink to="/overview" aria-label="智衡" class="platform-brand" @click="emit('navigate')">
        <BrandMark :compact="collapsed" />
      </RouterLink>
      <button
        v-if="showCollapseControl"
        type="button"
        class="platform-collapse-button"
        :aria-label="collapsed ? '展开侧栏' : '收起侧栏'"
        :title="collapsed ? '展开侧栏' : '收起侧栏'"
        @click="emit('toggle-collapse')"
      >
        <MenuUnfoldOutlined v-if="collapsed" /><MenuFoldOutlined v-else />
      </button>
    </div>
    <nav class="platform-primary-nav" aria-label="主导航">
      <RouterLink
        to="/overview"
        class="platform-nav-link"
        :class="{ 'is-active': active('/overview') }"
        :aria-current="active('/overview') ? 'page' : undefined"
        aria-label="工作台"
        :title="collapsed ? '工作台' : undefined"
        @click="emit('navigate')"
        ><HomeOutlined /><span v-if="!collapsed">工作台</span></RouterLink
      >
      <div v-for="group in groups" :key="group.id" class="platform-nav-group">
        <button
          v-if="!collapsed"
          type="button"
          class="platform-nav-group-title"
          :aria-label="group.title"
          :aria-expanded="isGroupExpanded(group)"
          :aria-controls="`${navigationId}-${group.id}`"
          :disabled="isGroupActive(group)"
          @click="toggleGroup(group)"
        >
          <span>{{ group.title }}</span>
          <RightOutlined
            aria-hidden="true"
            class="platform-nav-group-chevron"
            :class="{ 'is-expanded': isGroupExpanded(group) }"
          />
        </button>
        <div
          v-show="isGroupExpanded(group)"
          :id="`${navigationId}-${group.id}`"
          role="group"
          :aria-label="group.title"
        >
          <RouterLink
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="platform-nav-link"
            :class="{ 'is-active': active(item.path) }"
            :aria-current="active(item.path) ? 'page' : undefined"
            :aria-label="item.label"
            :title="collapsed ? item.label : undefined"
            @click="emit('navigate')"
          >
            <component :is="item.icon" /><span v-if="!collapsed">{{ item.label }}</span>
          </RouterLink>
        </div>
      </div>
    </nav>
    <div class="platform-sidebar__account">
      <PlatformAccountMenu
        variant="sidebar"
        :collapsed="collapsed"
        :leaving="leaving"
        @logout="emit('logout')"
      />
    </div>
  </aside>
</template>
<style scoped>
.platform-sidebar {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: var(--bg-sider);
  border-right: 0;
  color: var(--color-text);
}
.platform-brand-row {
  display: flex;
  align-items: center;
  min-height: var(--platform-topbar-height);
  padding: 0 16px 0 22px;
  gap: 8px;
}
.platform-brand {
  text-decoration: none;
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1;
}
.platform-collapse-button {
  display: grid;
  place-items: center;
  width: 26px;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  color: var(--color-text-tertiary);
  background: transparent;
  cursor: pointer;
}
.platform-collapse-button:hover {
  background: var(--gray-50);
  color: var(--main-color);
}
.platform-primary-nav {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 12px 20px;
  scrollbar-width: thin;
}
.platform-nav-group {
  margin-top: 14px;
}
.platform-nav-group-title {
  display: flex;
  width: 100%;
  min-height: 30px;
  align-items: center;
  justify-content: space-between;
  padding: 4px 14px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.platform-nav-group-title:hover:not(:disabled) {
  background: var(--gray-50);
}
.platform-nav-group-title:disabled {
  cursor: default;
}
.platform-nav-group-chevron {
  font-size: 10px;
}
.platform-nav-group-chevron.is-expanded {
  transform: rotate(90deg);
}
.platform-nav-link {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 40px;
  margin: 3px 0;
  padding: 0 14px;
  border-radius: 6px;
  color: var(--gray-800);
  font-size: 14px;
  font-weight: 400;
  text-decoration: none;
}
.platform-nav-link :deep(.anticon) {
  font-size: 18px;
}
.platform-nav-link:hover {
  background: var(--gray-50);
  color: var(--main-800);
}
.platform-nav-link.is-active {
  background: var(--main-50);
  color: var(--main-color);
  font-weight: 600;
}
.platform-sidebar__account {
  margin-top: auto;
  border-top: 0;
  padding: 12px;
}
.collapsed .platform-brand-row {
  flex-direction: column;
  padding: 12px 0 4px;
  gap: 6px;
}
.collapsed .platform-brand {
  text-decoration: none;
  flex: 0;
}
.collapsed .platform-primary-nav {
  padding: 8px;
}
.collapsed .platform-nav-link {
  justify-content: center;
  padding: 0;
  margin: 6px 0;
}
.collapsed .platform-nav-group {
  margin-top: 0;
}
.collapsed .platform-sidebar__account {
  padding: 10px 8px;
}
.platform-nav-link:focus-visible,
.platform-nav-group-title:focus-visible,
.platform-collapse-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
