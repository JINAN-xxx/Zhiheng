<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  AppstoreOutlined,
  ApiOutlined,
  BarChartOutlined,
  DatabaseOutlined,
  FileOutlined,
  HomeOutlined,
  MessageOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  TeamOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined
} from '@ant-design/icons-vue'
import { identity } from '../../modules/identity/public.ts'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
import BrandMark from '../../shared/components/BrandMark.vue'
withDefaults(
  defineProps<{ collapsed: boolean; showCollapseControl?: boolean; leaving?: boolean }>(),
  { leaving: false }
)
const emit = defineEmits<{ 'toggle-collapse': []; navigate: []; logout: [] }>()
const route = useRoute()
const groups = computed(() => [
  {
    title: '',
    items: [
      { path: '/overview', label: '工作台', icon: HomeOutlined },
      { path: '/chat', label: '对话体验', icon: MessageOutlined }
    ]
  },
  {
    title: '模型服务',
    items: identity.value?.is_admin
      ? [{ path: '/models', label: '模型目录', icon: AppstoreOutlined }]
      : []
  },
  {
    title: '应用构建',
    items: [
      { path: '/agents', label: '智能体', icon: RobotOutlined },
      { path: '/skills', label: '技能管理', icon: ThunderboltOutlined },
      { path: '/work', label: '工作空间', icon: TeamOutlined }
    ]
  },
  {
    title: '知识与数据',
    items: [
      { path: '/knowledge', label: '知识库', icon: DatabaseOutlined },
      ...(identity.value?.is_admin
        ? [{ path: '/files', label: '文件管理', icon: FileOutlined }]
        : [])
    ]
  },
  {
    title: '开发与用量',
    items: [
      { path: '/developer', label: '开发接入', icon: ApiOutlined },
      { path: '/usage', label: '用量与调用', icon: BarChartOutlined }
    ]
  }
])
function active(path: string): boolean {
  return route.path === path || route.path.startsWith(path + '/') || route.meta.activeMenu === path
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
      <template v-for="group in groups" :key="group.title">
        <div
          v-if="group.title && group.items.length && !collapsed"
          class="platform-nav-group-title"
        >
          {{ group.title }}
        </div>
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
      </template>
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
  border-right: 1px solid var(--gray-150);
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
.platform-nav-group-title {
  padding: 22px 14px 8px;
  color: var(--color-text-tertiary);
  font-size: 12px;
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
  border-top: 1px solid var(--gray-150);
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
.collapsed .platform-sidebar__account {
  padding: 10px 8px;
}
.platform-nav-link:focus-visible,
.platform-collapse-button:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
</style>
