<template>
  <aside class="platform-sidebar" :class="{ collapsed }" aria-label="智能工作台导航">
    <div class="platform-brand-row">
      <router-link class="platform-brand" to="/ai/chat" aria-label="返回智能工作台">
        <img :src="logo" alt="" />
        <span v-if="!collapsed">智衡平台</span>
      </router-link>
      <button
        v-if="showCollapseControl"
        type="button"
        class="platform-collapse-button"
        :aria-label="collapsed ? '展开侧边栏' : '折叠侧边栏'"
        :title="collapsed ? '展开侧边栏' : '折叠侧边栏'"
        @click="emit('toggle-collapse')"
      >
        <MenuUnfoldOutlined v-if="collapsed" />
        <MenuFoldOutlined v-else />
      </button>
    </div>

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
      <template v-for="group in resourceGroups" :key="group.key">
        <div v-if="!collapsed" class="platform-nav-group">
          <button
            type="button"
            class="platform-nav-group-link"
            :aria-label="group.label"
            :aria-expanded="resourceGroupIsExpanded(group)"
            @click="toggleResourceGroup(group)"
          >
            <component :is="resourceGroupIcon(group)" />
            <span>{{ group.label }}</span>
            <DownOutlined v-if="resourceGroupIsExpanded(group)" class="platform-nav-group-chevron" />
            <RightOutlined v-else class="platform-nav-group-chevron" />
          </button>
          <div v-show="resourceGroupIsExpanded(group)" class="platform-nav-group-children">
            <router-link
              v-for="child in group.children"
              :key="child.path"
              class="platform-nav-link platform-nav-child"
              :to="child.path"
              :aria-label="child.label"
              :aria-current="route.path === child.path ? 'page' : undefined"
            >
              <span>{{ child.label }}</span>
            </router-link>
          </div>
        </div>
        <a-dropdown
          v-else
          class="platform-nav-group-dropdown"
          :trigger="['click']"
          placement="rightTop"
        >
          <button
            type="button"
            class="platform-nav-link platform-nav-group-collapsed"
            :class="{ active: resourceGroupIsActive(group) }"
            :aria-label="group.label"
            aria-haspopup="menu"
            :title="group.label"
          >
            <component :is="resourceGroupIcon(group)" />
          </button>
          <template #overlay>
            <a-menu :selected-keys="[getActiveAiResourceChildPath(group, route.path)].filter(Boolean)" @click="handleResourceMenuClick">
              <a-menu-item v-for="child in group.children" :key="child.path">
                {{ child.label }}
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </template>
    </nav>

    <RecentSessions
      v-if="canChat && !collapsed"
      :sessions="workspaceStore.sessions"
      :active-session-id="activeSessionId"
      :loading="workspaceStore.sessionsLoading"
      :error="workspaceStore.sessionsError"
      @refresh="workspaceStore.loadSessions"
      @open-session="openSession"
      @rename-session="renameSession"
      @delete-session="deleteSession"
    />

    <button type="button" class="platform-admin-entry" aria-label="管理后台" title="管理后台" @click="emit('open-admin')">
      <SettingOutlined />
      <span v-if="!collapsed">管理后台</span>
    </button>
    <PlatformAccountMenu
      v-if="showAccountMenu"
      :collapsed="collapsed"
      @set-layout="emit('set-layout')"
    />
  </aside>
</template>

<script setup>
import {
  DatabaseOutlined,
  DownOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue'
import { ref, watch } from 'vue'
import logo from '@/assets/logo/logo.png'
import {
  getActiveAiResourceChildPath,
  isAiResourceGroupActive,
  resolveAgentExtensionGroups,
  resolveAiResourceGroups,
} from '@/components/Workspace/workspaceTransforms.js'
import feedback from '@/plugins/antdFeedback'
import useUserStore from '@/store/modules/user'
import useWorkspaceStore from '@/store/modules/workspace'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
import RecentSessions from './RecentSessions.vue'

defineProps({
  collapsed: { type: Boolean, default: false },
  showAccountMenu: { type: Boolean, default: false },
  showCollapseControl: { type: Boolean, default: false },
})
const emit = defineEmits(['open-admin', 'set-layout', 'toggle-collapse'])
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const workspaceStore = useWorkspaceStore()

const permissionSet = computed(() => new Set(userStore.permissions))
const canChat = computed(
  () => permissionSet.value.has('*:*:*') || permissionSet.value.has('ai:chat:list'),
)
const resourceGroups = computed(() => [
  ...resolveAiResourceGroups(permissionSet.value),
  ...resolveAgentExtensionGroups(permissionSet.value),
])
const expandedGroupKeys = ref(new Set(['model-center', 'agent-extension']))
const navItems = computed(() => [
  canChat.value && { path: '/ai/chat', label: '智能对话', icon: MessageOutlined },
].filter(Boolean))
const activeSessionId = computed(() => String(route.query.sessionId || ''))

function newChat() {
  router.push('/ai/chat')
}

function openSession(sessionId) {
  router.push({ path: '/ai/chat', query: { sessionId } })
}

function resourceGroupIsActive(group) {
  return isAiResourceGroupActive(group, route.path)
}

function resourceGroupIsExpanded(group) {
  return expandedGroupKeys.value.has(group.key)
}

function resourceGroupIcon(group) {
  return group.key === 'agent-extension' ? ThunderboltOutlined : DatabaseOutlined
}

function toggleResourceGroup(group) {
  const next = new Set(expandedGroupKeys.value)
  if (next.has(group.key)) next.delete(group.key)
  else next.add(group.key)
  expandedGroupKeys.value = next
}

function handleResourceMenuClick({ key }) {
  router.push(String(key))
}

async function renameSession(sessionId) {
  try {
    const result = await feedback.prompt('请输入新的会话名称')
    const title = String(result?.value || '').trim()
    if (!title) {
      feedback.msgWarning('会话名称不能为空')
      return
    }
    if (title.length > 200) {
      feedback.msgWarning('会话名称不能超过 200 字')
      return
    }
    await workspaceStore.renameSession(sessionId, title)
    feedback.msgSuccess('会话已重命名')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
  }
}

async function deleteSession(sessionId) {
  try {
    await feedback.confirm('删除后无法恢复，确定删除这个会话吗？')
    await workspaceStore.deleteSession(sessionId)
    if (activeSessionId.value === sessionId) await router.push('/ai/chat')
    feedback.msgSuccess('会话已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
  }
}

onMounted(() => {
  if (canChat.value && !workspaceStore.sessions.length) workspaceStore.loadSessions()
})

watch(() => route.path, () => {
  const activeGroup = resourceGroups.value.find((group) => resourceGroupIsActive(group))
  if (!activeGroup || expandedGroupKeys.value.has(activeGroup.key)) return
  expandedGroupKeys.value = new Set([...expandedGroupKeys.value, activeGroup.key])
})
</script>

<style scoped lang="scss">
.platform-sidebar { width: 100%; height: 100%; min-height: 0; display: flex; flex-direction: column; overflow: hidden; margin: 0; padding: 0; border-radius: 0; background: var(--bg-sider); border-right: 1px solid var(--gray-150); color: var(--color-text); font-family: var(--workspace-font-family); line-height: normal; }
.platform-brand-row { min-height: 56px; display: flex; align-items: center; gap: 8px; padding: 0 10px 0 16px; }
.platform-brand { min-width: 0; display: flex; flex: 1; align-items: center; gap: 9px; color: var(--color-text); font-size: 15px; font-weight: 700; text-decoration: none; }
.platform-brand img { width: 27px; height: 27px; object-fit: contain; filter: brightness(1.12); }
.platform-collapse-button { width: 30px; height: 30px; display: inline-flex; flex: 0 0 30px; align-items: center; justify-content: center; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.platform-collapse-button:hover { border-color: var(--gray-150); background: var(--gray-50); color: var(--color-text); }
.platform-primary-nav { display: grid; gap: 3px; padding: 6px 10px 14px; border-bottom: 1px solid var(--gray-150); }
.platform-nav-group,
.platform-nav-group-children { display: grid; gap: 2px; }
.platform-nav-link,
.platform-nav-group-link,
.platform-new-chat,
.platform-admin-entry { height: var(--platform-menu-item-height); min-height: var(--platform-menu-item-height); display: flex; align-items: center; gap: 10px; padding: 0 10px; border: 1px solid transparent; border-radius: var(--workspace-radius-sm); background: transparent; color: var(--color-text-secondary); text-decoration: none; cursor: pointer; white-space: nowrap; font-family: inherit; font-size: 14px; line-height: 20px; font-weight: 400; }
.platform-nav-group-link { height: var(--platform-menu-item-height); min-height: var(--platform-menu-item-height); color: var(--color-text); font-weight: 500; justify-content: flex-start; text-align: left; }
.platform-nav-group-chevron { margin-left: auto; font-size: 11px; color: var(--color-text-tertiary); }
.platform-nav-child { padding-left: 34px; }
.platform-new-chat { margin-bottom: 5px; border-color: var(--gray-150); background: var(--bg-container); color: var(--color-text); }
.platform-nav-link:hover,
.platform-nav-group-link:hover,
.platform-admin-entry:hover { background: var(--gray-50); color: var(--color-text); }
.platform-nav-link.router-link-active { background: var(--main-50); color: var(--main-700); }
.platform-nav-group-collapsed.active { background: var(--main-50); color: var(--main-700); }
.platform-admin-entry { margin: 8px 10px; border-top: 1px solid var(--gray-150); border-radius: 0; padding-top: 8px; }
.platform-sidebar.collapsed .platform-brand-row { gap: 0; padding-inline: 0; }
.platform-sidebar.collapsed .platform-brand { justify-content: center; }
.platform-sidebar.collapsed .platform-brand { width: 26px; flex: 0 0 26px; }
.platform-sidebar.collapsed .platform-brand img { width: 23px; height: 23px; }
.platform-sidebar.collapsed .platform-nav-link,
.platform-sidebar.collapsed .platform-new-chat,
.platform-sidebar.collapsed .platform-admin-entry { justify-content: center; padding-inline: 0; }
</style>
