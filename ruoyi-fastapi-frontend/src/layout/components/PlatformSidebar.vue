<template>
  <aside class="platform-sidebar" :class="{ collapsed }" aria-label="AI 工作台导航">
    <router-link class="platform-brand" to="/ai/chat" aria-label="返回 AI 工作台">
      <img :src="logo" alt="" />
      <span v-if="!collapsed">智衡 AI</span>
    </router-link>

    <nav class="platform-primary-nav" aria-label="AI 主导航">
      <button v-if="canChat" type="button" class="platform-new-chat" aria-label="新建对话" title="新建对话" @click="newChat">
        <el-icon><Plus /></el-icon>
        <span v-if="!collapsed">新建对话</span>
      </button>
      <router-link
        v-if="canChat"
        class="platform-nav-link"
        to="/ai/chat"
        aria-label="AI 对话"
        :aria-current="route.path === '/ai/chat' ? 'page' : undefined"
      >
        <el-icon><ChatDotRound /></el-icon>
        <span v-if="!collapsed">AI 对话</span>
      </router-link>
      <router-link
        v-if="resourceTabs.length"
        class="platform-nav-link"
        :to="resourceTabs[0].path"
        aria-label="AI 资源"
        :aria-current="resourceTabs.some((tab) => tab.path === route.path) ? 'page' : undefined"
      >
        <el-icon><Box /></el-icon>
        <span v-if="!collapsed">AI 资源</span>
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

    <button type="button" class="platform-admin-entry" aria-label="管理控制台" title="管理控制台" @click="emit('open-admin')">
      <el-icon><Setting /></el-icon>
      <span v-if="!collapsed">管理控制台</span>
    </button>
  </aside>
</template>

<script setup>
import { Box, ChatDotRound, Plus, Setting } from '@element-plus/icons-vue'
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
.platform-sidebar { height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; background: var(--workspace-surface); border-right: 1px solid var(--workspace-border); }
.platform-brand { min-height: var(--platform-topbar-height); display: flex; align-items: center; gap: 10px; padding: 0 14px; color: var(--workspace-text); font-size: 15px; font-weight: 650; text-decoration: none; }
.platform-brand img { width: 30px; height: 30px; object-fit: contain; }
.platform-primary-nav { display: grid; gap: 4px; padding: 8px 10px 10px; border-bottom: 1px solid var(--workspace-border); }
.platform-nav-link,
.platform-new-chat,
.platform-admin-entry { height: var(--platform-menu-item-height); min-height: var(--platform-menu-item-height); display: flex; align-items: center; gap: 10px; padding: 0 10px; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--workspace-text-secondary); text-decoration: none; cursor: pointer; }
.platform-new-chat { border-color: var(--workspace-border); color: var(--workspace-text); }
.platform-nav-link.router-link-active { background: var(--workspace-main-soft); color: var(--workspace-main-deep); }
.platform-admin-entry { margin: 8px 10px 12px; }
.platform-sidebar.collapsed .platform-brand,
.platform-sidebar.collapsed .platform-nav-link,
.platform-sidebar.collapsed .platform-new-chat,
.platform-sidebar.collapsed .platform-admin-entry { justify-content: center; padding-inline: 0; }
</style>
