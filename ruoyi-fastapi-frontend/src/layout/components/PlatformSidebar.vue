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
      <button v-if="canCreateConversation" type="button" class="platform-new-chat" aria-label="新建对话" title="新建对话" @click="newConversation">
        <PlusOutlined />
        <span v-if="!collapsed">新建对话</span>
      </button>
      <router-link
        v-for="item in navItems"
        :key="item.path"
        class="platform-nav-link"
        :class="{ 'router-link-active': item.active }"
        :to="item.path"
        :aria-label="item.label"
        :aria-current="item.active ? 'page' : undefined"
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
      v-if="canChat && !collapsed && conversationMode === 'chat'"
      :sessions="workspaceStore.sessions"
      :active-session-id="activeSessionId"
      :loading="workspaceStore.sessionsLoading"
      :error="workspaceStore.sessionsError"
      :status="workspaceStore.sessionStatus"
      :page-num="workspaceStore.sessionsPageNum"
      :page-size="workspaceStore.sessionsPageSize"
      :total="workspaceStore.sessionsTotal"
      :mutating-ids="workspaceStore.sessionMutatingIds"
      @refresh="workspaceStore.loadSessions"
      @open-session="openSession"
      @rename-session="renameSession"
      @delete-session="deleteSession"
      @archive-session="archiveSession"
      @restore-session="restoreSession"
      @status-change="status => workspaceStore.loadSessions({ status, pageNum: 1 })"
      @page-change="pageNum => workspaceStore.loadSessions({ pageNum })"
    />
    <RecentWorkTasks
      v-if="workCapabilities.list && !collapsed && conversationMode === 'work'"
      :tasks="workTaskStore.tasks"
      :active-task-id="activeTaskId"
      :loading="workTaskStore.listLoading"
      :error="workTaskStore.listError"
      :total="workTaskStore.total"
      :page-num="workTaskStore.pageNum"
      :page-size="workTaskStore.pageSize"
      :keyword="workTaskStore.listQuery.keyword"
      :status="workTaskStore.listQuery.status"
      :can-open="workCapabilities.query"
      :can-edit="workCapabilities.edit"
      :can-remove="workCapabilities.remove"
      @filter="workTaskStore.loadTasks"
      @open="openWork"
      @rename="renameWork"
      @delete="deleteWork"
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
  resolveConversationWorkspaceMode,
} from '@/components/Workspace/workspaceTransforms.js'
import { resolveWorkTaskCapabilities } from '../../../plugins/agent_extension/utils/workTask.js'
import feedback from '@/plugins/antdFeedback'
import useUserStore from '@/store/modules/user'
import useWorkspaceStore from '@/store/modules/workspace'
import useWorkTaskStore from '@/store/modules/workTask'
import PlatformAccountMenu from './PlatformAccountMenu.vue'
import RecentSessions from './RecentSessions.vue'
import RecentWorkTasks from './RecentWorkTasks.vue'

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
const workTaskStore = useWorkTaskStore()

const permissionSet = computed(() => new Set(userStore.permissions))
const canChat = computed(
  () => permissionSet.value.has('*:*:*') || permissionSet.value.has('ai:chat:list'),
)
const workCapabilities = computed(() => resolveWorkTaskCapabilities(permissionSet.value))
const conversationMode = computed(() => resolveConversationWorkspaceMode(route.path))
const resourceGroups = computed(() => [
  ...resolveAiResourceGroups(permissionSet.value),
  ...resolveAgentExtensionGroups(permissionSet.value),
])
const expandedGroupKeys = ref(new Set())
const conversationPath = computed(() => conversationMode.value === 'work' || !canChat.value ? '/ai/chat/work' : '/ai/chat')
const canCreateConversation = computed(() => conversationPath.value === '/ai/chat/work' ? workCapabilities.value.canCreate : canChat.value)
const navItems = computed(() => [
  (canChat.value || workCapabilities.value.visible) && {
    path: conversationPath.value,
    label: '智能对话',
    icon: MessageOutlined,
    active: route.path === '/ai/chat' || route.path.startsWith('/ai/chat/'),
  },
].filter(Boolean))
const activeSessionId = computed(() => String(route.query.sessionId || ''))
const activeTaskId = computed(() => String(route.query.taskId || ''))

function newConversation() {
  if (conversationPath.value === '/ai/chat') workspaceStore.loadSessions({ status: 'active', pageNum: 1 })
  router.push(conversationPath.value)
}

function openWork(taskId) { router.push({ path: '/ai/chat/work', query: { taskId } }) }

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
    if (!await workspaceStore.deleteSession(sessionId)) return
    if (activeSessionId.value === sessionId) await router.push('/ai/chat')
    feedback.msgSuccess('会话已删除')
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
  }
}

async function archiveSession(sessionId) {
  try {
    if (!await workspaceStore.setSessionStatus(sessionId, 'archived')) return
    if (activeSessionId.value === sessionId) await router.push('/ai/chat')
    feedback.msgSuccess('会话已归档，可在“已归档”中查看和恢复')
  } catch {
    // The shared request client displays the server's ownership/busy error.
  }
}

async function restoreSession(sessionId) {
  try {
    if (!await workspaceStore.setSessionStatus(sessionId, 'active')) return
    await workspaceStore.loadSessions({ status: 'active', pageNum: 1 })
    feedback.msgSuccess('对话已恢复')
  } catch {
    // The shared request client displays API errors; failed actions retain the row.
  }
}

async function renameWork(task) {
  try {
    const result = await feedback.prompt('请输入新的工作标题')
    const title = String(result?.value || '').trim()
    if (!title || title.length > 100) return feedback.msgWarning('工作标题须为 1 至 100 个字符')
    await workTaskStore.renameListedTask(task, title)
  } catch (error) { if (error !== 'cancel' && error !== 'close') feedback.msgError(error?.message || '重命名失败') }
}

async function deleteWork(task) {
  try {
    await feedback.confirm('删除后无法恢复，确定删除这个工作吗？')
    await workTaskStore.removeTask(task.taskId)
    if (activeTaskId.value === task.taskId) await router.push('/ai/chat/work')
  } catch (error) { if (error !== 'cancel' && error !== 'close') feedback.msgError(error?.message || '删除失败') }
}

onMounted(() => {
  if (conversationMode.value === 'chat' && canChat.value && !workspaceStore.sessions.length) workspaceStore.loadSessions()
  if (conversationMode.value === 'work' && workCapabilities.value.list && !workTaskStore.tasks.length) workTaskStore.loadTasks()
})

watch(conversationMode, (mode) => {
  if (mode === 'chat' && canChat.value && !workspaceStore.sessions.length) workspaceStore.loadSessions()
  if (mode === 'work' && workCapabilities.value.list && !workTaskStore.tasks.length) workTaskStore.loadTasks()
})

watch([resourceGroups, () => route.path], ([groups]) => {
  const activeGroup = groups.find((group) => resourceGroupIsActive(group))
  if (!activeGroup || expandedGroupKeys.value.has(activeGroup.key)) return
  expandedGroupKeys.value = new Set([...expandedGroupKeys.value, activeGroup.key])
}, { immediate: true })
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
