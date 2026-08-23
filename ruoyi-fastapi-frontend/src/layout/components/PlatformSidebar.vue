<template>
  <aside class="platform-sidebar" :class="{ collapsed }" aria-label="智能工作台导航">
    <router-link class="platform-brand" to="/ai/chat" aria-label="返回智能工作台">
      <img :src="logo" alt="" />
      <span v-if="!collapsed">智衡平台</span>
    </router-link>

    <nav class="platform-primary-nav" aria-label="主导航">
      <button v-if="canChat" type="button" class="platform-new-chat" aria-label="新建对话" title="新建对话" @click="newChat">
        <PlusOutlined />
        <span v-if="!collapsed">新建对话</span>
      </button>
      <router-link
        v-for="item in navItems"
        :key="item.path"
        class="platform-nav-link"
        :to="item.path"
        :aria-label="item.label"
        :aria-current="route.path === item.path ? 'page' : undefined"
      >
        <component :is="item.icon" />
        <span v-if="!collapsed">{{ item.label }}</span>
      </router-link>
    </nav>

    <RecentSessions
      v-if="canChat && !collapsed"
      :sessions="workspaceStore.sessions"
      :active-session-id="activeSessionId"
      :loading="workspaceStore.sessionsLoading"
      :error="workspaceStore.sessionsError"
      @refresh="workspaceStore.loadSessions"
      @open-session="openSession"
      @archive-session="archiveSession"
    />

    <button type="button" class="platform-admin-entry" aria-label="管理后台" title="管理后台" @click="emit('open-admin')">
      <SettingOutlined />
      <span v-if="!collapsed">管理后台</span>
    </button>
  </aside>
</template>

<script setup>
import { BarChartOutlined, CodeOutlined, DatabaseOutlined, MessageOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons-vue'
import logo from '@/assets/logo/logo.png'
import { resolveAiResourceTabs } from '@/components/Workspace/workspaceTransforms.js'
import useUserStore from '@/store/modules/user'
import useWorkspaceStore from '@/store/modules/workspace'
import RecentSessions from './RecentSessions.vue'

defineProps({ collapsed: { type: Boolean, default: false } })
const emit = defineEmits(['open-admin'])
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const workspaceStore = useWorkspaceStore()

const permissionSet = computed(() => new Set(userStore.permissions))
const canChat = computed(
  () => permissionSet.value.has('*:*:*') || permissionSet.value.has('ai:chat:list'),
)
const resourceTabs = computed(() => resolveAiResourceTabs(permissionSet.value))
const resourceIcons = Object.freeze({
  '/ai/model': DatabaseOutlined,
  '/ai/application': CodeOutlined,
  '/ai/usage': BarChartOutlined,
})
const navItems = computed(() => [
  canChat.value && { path: '/ai/chat', label: '智能对话', icon: MessageOutlined },
  ...resourceTabs.value.map((tab) => ({ ...tab, icon: resourceIcons[tab.path] || DatabaseOutlined })),
].filter(Boolean))
const activeSessionId = computed(() => String(route.query.sessionId || ''))

function newChat() {
  router.push('/ai/chat')
}

function openSession(sessionId) {
  router.push({ path: '/ai/chat', query: { sessionId } })
}

async function archiveSession(sessionId) {
  await workspaceStore.archiveSession(sessionId)
  if (activeSessionId.value === sessionId) await router.push('/ai/chat')
}

onMounted(() => {
  if (canChat.value && !workspaceStore.sessions.length) workspaceStore.loadSessions()
})
</script>

<style scoped lang="scss">
.platform-sidebar { height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; background: var(--workspace-sidebar-bg); border-right: 1px solid var(--workspace-sidebar-border); color: var(--workspace-sidebar-text); }
.platform-brand { min-height: 66px; display: flex; align-items: center; gap: 9px; padding: 0 16px; color: #fff; font-size: 15px; font-weight: 700; text-decoration: none; }
.platform-brand img { width: 27px; height: 27px; object-fit: contain; filter: brightness(1.12); }
.platform-primary-nav { display: grid; gap: 3px; padding: 6px 10px 14px; border-bottom: 1px solid var(--workspace-sidebar-border); }
.platform-nav-link,
.platform-new-chat,
.platform-admin-entry { height: var(--platform-menu-item-height); min-height: var(--platform-menu-item-height); display: flex; align-items: center; gap: 10px; padding: 0 10px; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--workspace-sidebar-muted); text-decoration: none; cursor: pointer; white-space: nowrap; }
.platform-new-chat { margin-bottom: 5px; border-color: var(--workspace-sidebar-border); background: var(--workspace-sidebar-surface); color: #fff; }
.platform-nav-link:hover,
.platform-admin-entry:hover { background: var(--workspace-sidebar-surface); color: #fff; }
.platform-nav-link.router-link-active { background: var(--workspace-main); color: #fff; }
.platform-admin-entry { margin: 8px 10px 12px; border-top: 1px solid var(--workspace-sidebar-border); border-radius: 0; padding-top: 8px; }
.platform-sidebar.collapsed .platform-brand,
.platform-sidebar.collapsed .platform-nav-link,
.platform-sidebar.collapsed .platform-new-chat,
.platform-sidebar.collapsed .platform-admin-entry { justify-content: center; padding-inline: 0; }
</style>
