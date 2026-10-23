<!-- Adapted from original PlatformSidebar.vue, MIT licensed. -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { Dropdown as ADropdown, Menu as AMenu, MenuItem as AMenuItem } from 'ant-design-vue'
import {
  SettingOutlined,
  DatabaseOutlined,
  DownOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined,
  ThunderboltOutlined
} from '@ant-design/icons-vue'
import { ChatNavigation, useChatWorkspace } from '../../modules/chat/public.ts'
import { identity } from '../../modules/identity/public.ts'
import logo from '../../shared/assets/logo.png'
defineProps<{ collapsed: boolean; showCollapseControl?: boolean }>()
const emit = defineEmits<{ 'toggle-collapse': []; navigate: [] }>()
const route = useRoute()
const router = useRouter()
const resourcesExpanded = ref(route.name === 'models' || route.name === 'usage')
const historyExpanded = ref(false)
watch(
  () => route.path,
  () => {
    historyExpanded.value = false
    if (route.name === 'models' || route.name === 'usage') resourcesExpanded.value = true
  }
)
const { selectConversation } = useChatWorkspace()
async function newConversation(): Promise<void> {
  await selectConversation(null)
  await router.push('/chat')
  emit('navigate')
}
async function openHistory(): Promise<void> {
  if (route.name !== 'chat') await router.push('/chat')
  emit('navigate')
}
</script>
<template>
  <aside class="platform-sidebar" :class="{ collapsed }">
    <div class="platform-brand-row">
      <RouterLink to="/chat" class="platform-brand" aria-label="智衡平台" @click="emit('navigate')"
        ><img :src="logo" alt="智衡平台" class="platform-brand-logo" /><span
          v-if="!collapsed"
          class="platform-brand-text"
          >智衡平台</span
        ></RouterLink
      >
      <button
        v-if="showCollapseControl"
        type="button"
        class="platform-collapse-button"
        :aria-label="collapsed ? '展开侧栏' : '收起侧栏'"
        @click="emit('toggle-collapse')"
      >
        <MenuUnfoldOutlined v-if="collapsed" /><MenuFoldOutlined v-else />
      </button>
    </div>
    <nav class="platform-primary-nav" aria-label="主导航">
      <button
        type="button"
        class="platform-new-chat"
        aria-label="新建对话"
        @click="newConversation"
      >
        <PlusOutlined /><span v-if="!collapsed">新建对话</span>
      </button>
      <RouterLink
        to="/chat"
        class="platform-nav-link"
        :class="{ 'router-link-active': route.name === 'chat' }"
        aria-label="智能对话"
        @click="emit('navigate')"
        ><MessageOutlined /><span v-if="!collapsed">智能对话</span></RouterLink
      >
      <div class="platform-nav-group">
        <button
          v-if="!collapsed"
          type="button"
          class="platform-nav-group-link"
          aria-label="模型中心"
          :aria-expanded="resourcesExpanded"
          @click="resourcesExpanded = !resourcesExpanded"
        >
          <DatabaseOutlined /><span>模型中心</span
          ><DownOutlined
            v-if="resourcesExpanded"
            class="platform-nav-group-chevron"
          /><RightOutlined v-else class="platform-nav-group-chevron" />
        </button>
        <div v-if="!collapsed && resourcesExpanded" class="platform-nav-group-children">
          <RouterLink
            v-if="identity?.is_admin"
            to="/models"
            class="platform-nav-link"
            :class="{
              'platform-nav-child': !collapsed,
              'router-link-active': route.name === 'models'
            }"
            aria-label="模型目录"
            @click="emit('navigate')"
            ><span>模型目录</span></RouterLink
          >
          <button
            type="button"
            class="platform-nav-link platform-nav-child"
            disabled
            title="当前版本暂不支持开发接入"
          >
            开发接入
          </button>
          <RouterLink
            to="/usage"
            class="platform-nav-link"
            :class="{
              'platform-nav-child': !collapsed,
              'router-link-active': route.name === 'usage'
            }"
            aria-label="用量记录"
            @click="emit('navigate')"
            ><DatabaseOutlined v-if="collapsed" /><span v-else>用量记录</span></RouterLink
          >
        </div>
        <ADropdown v-if="collapsed" :trigger="['click']" placement="bottomLeft"
          ><button
            type="button"
            class="platform-nav-link platform-nav-group-collapsed"
            aria-label="模型中心"
          >
            <DatabaseOutlined /></button
          ><template #overlay
            ><AMenu
              ><AMenuItem v-if="identity?.is_admin"
                ><RouterLink to="/models" aria-label="模型目录">模型目录</RouterLink></AMenuItem
              ><AMenuItem disabled title="当前版本暂不支持开发接入">开发接入</AMenuItem
              ><AMenuItem
                ><RouterLink to="/usage" aria-label="用量记录">用量记录</RouterLink></AMenuItem
              ></AMenu
            ></template
          ></ADropdown
        >
      </div>
      <button
        type="button"
        class="platform-nav-link"
        disabled
        aria-label="智能体扩展"
        title="当前版本暂不支持智能体扩展"
      >
        <ThunderboltOutlined /><span v-if="!collapsed">智能体扩展</span
        ><RightOutlined v-if="!collapsed" class="platform-nav-group-chevron" />
      </button>
    </nav>
    <button
      v-if="!collapsed && route.name !== 'chat'"
      type="button"
      class="platform-history-toggle"
      :aria-label="historyExpanded ? '收起最近会话' : '展开最近会话'"
      :aria-expanded="historyExpanded"
      @click="historyExpanded = !historyExpanded"
    >
      <MessageOutlined /><span>最近会话</span
      ><DownOutlined v-if="historyExpanded" class="platform-nav-group-chevron" /><RightOutlined
        v-else
        class="platform-nav-group-chevron"
      />
    </button>
    <ChatNavigation
      v-if="!collapsed && (route.name === 'chat' || historyExpanded)"
      @navigate="openHistory"
    />
    <button
      type="button"
      class="platform-admin-entry"
      disabled
      aria-label="管理后台"
      title="当前版本暂不支持管理后台"
    >
      <SettingOutlined /><span v-if="!collapsed">管理后台</span>
    </button>
  </aside>
</template>
<style scoped lang="scss">
.platform-sidebar {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin: 0;
  padding: 0;
  border-radius: 0;
  background: var(--bg-sider);
  border-right: 1px solid var(--gray-150);
  color: var(--color-text);
  font-family: var(--workspace-font-family);
  line-height: normal;
}
.platform-brand-row {
  min-height: 56px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px 0 16px;
}
.platform-brand {
  min-width: 0;
  display: flex;
  flex: 1;
  align-items: center;
  gap: 8px;
  color: var(--color-text);
  text-decoration: none;
}
.platform-brand-logo {
  height: 40px;
  width: auto;
  object-fit: contain;
}
.platform-brand-text {
  font-size: 14px;
  font-weight: var(--workspace-font-weight-semibold);
  white-space: nowrap;
  color: var(--color-text);
  font-family: var(--workspace-font-family);
}
.platform-collapse-button {
  width: 30px;
  height: 30px;
  display: inline-flex;
  flex: 0 0 30px;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.platform-collapse-button:hover {
  border-color: var(--gray-150);
  background: var(--gray-50);
  color: var(--color-text);
}
.platform-primary-nav {
  display: grid;
  gap: 3px;
  padding: 6px 10px 14px;
  border-bottom: 1px solid var(--gray-150);
}
.platform-nav-group,
.platform-nav-group-children {
  display: grid;
  gap: 2px;
}
.platform-nav-link,
.platform-nav-group-link,
.platform-history-toggle,
.platform-new-chat,
.platform-admin-entry {
  height: var(--platform-menu-item-height);
  min-height: var(--platform-menu-item-height);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--workspace-radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
  font-family: inherit;
  font-size: 14px;
  line-height: 20px;
  font-weight: 400;
}
.platform-nav-group-link {
  height: var(--platform-menu-item-height);
  min-height: var(--platform-menu-item-height);
  color: var(--color-text);
  font-weight: 500;
  justify-content: flex-start;
  text-align: left;
}
.platform-nav-group-chevron {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-tertiary);
}
.platform-nav-child {
  padding-left: 34px;
}
.platform-new-chat {
  margin-bottom: 5px;
  border-color: var(--gray-150);
  background: var(--bg-container);
  color: var(--color-text);
}
.platform-nav-link:hover,
.platform-nav-group-link:hover,
.platform-history-toggle:hover,
.platform-admin-entry:hover {
  background: var(--gray-50);
  color: var(--color-text);
}
.platform-nav-link.router-link-active {
  background: var(--main-50);
  color: var(--main-700);
}
.platform-nav-group-collapsed.active {
  background: var(--main-50);
  color: var(--main-700);
}
.platform-history-toggle {
  margin: 8px 10px;
  text-align: left;
}
.platform-history-toggle:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 2px;
}
.platform-admin-entry {
  margin: auto 10px 8px;
  border-top: 1px solid var(--gray-150);
  border-radius: 0;
  padding-top: 8px;
}
.platform-sidebar.collapsed .platform-brand-row {
  gap: 0;
  padding-inline: 0;
}
.platform-sidebar.collapsed .platform-brand {
  justify-content: center;
}
.platform-sidebar.collapsed .platform-brand {
  width: 26px;
  flex: 0 0 26px;
}
.platform-sidebar.collapsed .platform-brand-logo {
  height: 28px;
  width: auto;
}
.platform-sidebar.collapsed .platform-nav-link,
.platform-sidebar.collapsed .platform-new-chat,
.platform-sidebar.collapsed .platform-admin-entry {
  justify-content: center;
  padding-inline: 0;
}
@media (min-width: 992px) {
  .platform-primary-nav {
    min-height: 0;
    overflow-y: auto;
  }
  .recent-sessions {
    min-height: 160px;
  }
}
</style>

<style scoped>
.platform-sidebar button:disabled {
  opacity: 0.45;
  cursor: default;
}
</style>
