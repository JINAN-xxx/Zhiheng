<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <main class="app-container agent-management-page">
    <WorkspaceToolbar
      class="agent-toolbar"
      title="智能助手"
      subtitle="创建、配置并发布智能助手，支持绑定技能与知识库"
    >
      <template #actions>
        <RefreshButton label="刷新智能助手" :loading="loading" @click="load" />
        <a-button
          v-if="canUseCapability('agents', 'create')"
          type="primary"
          aria-label="新建智能助手"
          @click="openEditor()"
          ><template #icon><PlusOutlined /></template>新建智能助手</a-button
        >
      </template>
    </WorkspaceToolbar>
    <section class="agent-content" :aria-busy="loading">
      <div class="agent-controls">
        <div class="agent-filter-summary">
          <div class="agent-status-filters" role="group" aria-label="按发布状态筛选智能助手">
            <button
              v-for="item in statusFilters"
              :key="item.value"
              type="button"
              class="agent-status-filter"
              :aria-label="item.label"
              :aria-pressed="statusFilter === item.value"
              @click="filterByStatus(item.value)"
            >
              {{ item.label }}
              <span>{{ statusCounts[item.value] ?? '—' }}</span>
            </button>
          </div>
          <span class="agent-total" aria-live="polite">共 {{ total }} 个</span>
        </div>
        <a-input-search
          v-model:value="query.keyword"
          :maxlength="100"
          allow-clear
          placeholder="搜索智能助手"
          aria-label="搜索智能助手"
          @search="search"
        />
      </div>
      <WorkspaceState v-if="loading && !agents.length" kind="loading" />
      <WorkspaceState
        v-else-if="error && !agents.length"
        kind="error"
        :message="error"
        @retry="load"
      />
      <WorkspaceState
        v-else-if="!agents.length"
        kind="empty"
        :title="
          statusFilter !== 'all' || query.keyword.trim() ? '没有匹配的智能助手' : '暂无智能助手'
        "
        :message="
          statusFilter !== 'all' || query.keyword.trim()
            ? '调整发布状态或搜索关键词后重试'
            : '创建一个智能助手，配置模型、技能与知识库后即可开始使用'
        "
      />
      <template v-else>
        <a-alert v-if="error" class="agent-alert" type="warning" :message="error" show-icon />
        <ResourceCardGrid class="agent-grid">
          <ResourceCard
            v-for="agent in agents"
            :key="agent.agentId"
            class="agent-card"
            :title="agent.name"
            :status="agent.status === 'published' ? '已发布' : '草稿'"
            :tone="agent.status === 'published' ? 'positive' : 'neutral'"
            :icon-tone="agent.status === 'published' ? 'primary' : 'neutral'"
            interactive
            :open-label="`配置${agent.name}`"
            @open="openEditor(agent)"
          >
            <template #icon><AgentIdentityIcon /></template>
            <p class="agent-card__description" :title="agent.description || '暂无描述'">
              {{ agent.description || '暂无描述，添加一段说明，让团队了解智能助手的用途。' }}
            </p>
            <div class="agent-card__metadata">
              <span><ThunderboltOutlined />{{ agent.skillSlugs.length }} 个技能</span>
              <span><DatabaseOutlined />{{ agent.knowledgeBaseIds.length }} 个知识库</span>
            </div>
            <p v-if="hasUnpublishedAgentChanges(agent)" class="agent-pending">有未发布更改</p>
            <template #footer-info>
              <span :title="agent.updatedAt ? formatUpdatedAt(agent.updatedAt, true) : ''">
                {{ agent.updatedAt ? `更新于 ${formatUpdatedAt(agent.updatedAt)}` : '已保存' }}
              </span>
            </template>
            <template #actions>
              <a-button
                v-if="canUseCapability('agents')"
                size="small"
                type="text"
                class="resource-card-primary"
                :loading="openingId === agent.agentId"
                aria-label="使用"
                @click="router.push(`/agents/${encodeURIComponent(agent.agentId)}/chat`)"
                ><MessageOutlined />使用</a-button
              >
              <a-button
                v-if="canUseCapability('agents', 'create') || canUseCapability('agents', 'publish')"
                size="small"
                type="text"
                class="agent-card__edit"
                :aria-label="canEdit(agent) ? '编辑智能助手' : '查看智能助手配置'"
                @click="openEditor(agent)"
                >{{ canEdit(agent) ? '编辑' : '查看' }}</a-button
              >
              <WorkspaceMoreMenu
                v-if="agentActions(agent).length"
                :items="agentActions(agent)"
                @command="agentCommand($event, agent)"
              />
            </template>
          </ResourceCard>
          <button
            v-if="
              canUseCapability('agents', 'create') &&
              displayedQuery.status === 'all' &&
              !displayedQuery.keyword.trim()
            "
            class="create-agent-card"
            type="button"
            @click="openEditor()"
          >
            <span class="create-agent-card__icon"><PlusOutlined /></span>
            <span>创建新的智能助手</span>
            <span class="create-agent-card__hint">配置专属助手，连接技能与知识库</span>
          </button>
        </ResourceCardGrid>
        <a-pagination
          v-if="total > query.pageSize"
          v-model:current="query.pageNum"
          :page-size="query.pageSize"
          :total="total"
          :show-size-changer="false"
          size="small"
          @change="load"
        />
      </template>
    </section>
    <AgentPreviewDrawer v-model="previewOpen" :agent="toolAgent" />
    <AgentApiDrawer v-model="apiOpen" :agent="toolAgent" />
  </main>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  InputSearch as AInputSearch,
  Pagination as APagination
} from 'ant-design-vue'
import {
  ApiOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  PlusOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceCard from '../../../shared/components/ResourceCard.vue'
import ResourceCardGrid from '../../../shared/components/ResourceCardGrid.vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getAgent, listAgents } from '../api/index.ts'
import type { Agent } from '../api/index.ts'
import { identity, canUseCapability, canUseModule } from '../../identity/public.ts'
import { hasUnpublishedAgentChanges } from '../presentation.ts'
import AgentPreviewDrawer from '../components/AgentPreviewDrawer.vue'
import AgentApiDrawer from '../components/AgentApiDrawer.vue'
import AgentIdentityIcon from '../components/AgentIdentityIcon.vue'
const agents = ref<Agent[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const query = reactive({ keyword: '', pageNum: 1, pageSize: 12 })
type StatusFilter = 'all' | Agent['status']
const statusFilters: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'published', label: '已发布' },
  { value: 'draft', label: '草稿' }
]
const statusFilter = ref<StatusFilter>('all')
const displayedQuery = reactive({ status: 'all' as StatusFilter, keyword: '' })
let countedKeyword = ''
const statusCounts = reactive<Record<StatusFilter, number | null>>({
  all: null,
  published: null,
  draft: null
})
const route = useRoute(),
  router = useRouter()
const previewOpen = ref(false),
  apiOpen = ref(false),
  toolAgent = ref<Agent | null>(null),
  openingId = ref('')
const listScope = new RequestScope(),
  toolScope = new RequestScope()
function canEdit(agent: Agent): boolean {
  return canUseCapability('agents', 'update') && identity.value?.id === agent.ownerId
}
function formatUpdatedAt(value: string, full = false): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return full
    ? date.toLocaleString('zh-CN', { hour12: false })
    : date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })
}
function agentActions(agent: Agent): MenuAction[] {
  return [
    ...(canUseCapability('agents', 'read')
      ? [{ command: 'configure', label: '配置', icon: SettingOutlined }]
      : []),
    ...(canUseCapability('agents', 'debug')
      ? [{ command: 'preview', label: '调试草稿', icon: MessageOutlined }]
      : []),
    ...(canUseModule('developer') && agent.status === 'published'
      ? [{ command: 'api', label: 'API', icon: ApiOutlined }]
      : [])
  ]
}
function agentCommand(command: string, agent: Agent): void {
  if (command === 'configure') openEditor(agent)
  else if (command === 'preview') void openAgentTool(agent, 'preview')
  else if (command === 'api' && agent.status === 'published') void openAgentTool(agent, 'api')
}
async function load(): Promise<void> {
  toolScope.replace()
  openingId.value = ''
  const context = listScope.replace()
  const filter = statusFilter.value
  const requestedQuery = {
    ...query,
    ...(filter === 'all' ? {} : { status: filter })
  }
  loading.value = true
  error.value = ''
  if (countedKeyword !== requestedQuery.keyword) {
    for (const item of statusFilters) statusCounts[item.value] = null
    countedKeyword = requestedQuery.keyword
  }
  try {
    const page = await listAgents(requestedQuery, context.signal)
    if (!context.isCurrent()) return
    agents.value = page.rows
    total.value = page.total
    displayedQuery.status = filter
    displayedQuery.keyword = requestedQuery.keyword
    statusCounts[filter] = page.total
    if (!page.rows.length && query.pageNum > 1) {
      query.pageNum -= 1
      await load()
      return
    }
    if (filter === 'all' && page.rows.length === page.total) {
      statusCounts.published = page.rows.filter((agent) => agent.status === 'published').length
      statusCounts.draft = page.total - statusCounts.published
    } else {
      const otherFilters = statusFilters.filter((item) => item.value !== filter)
      void Promise.allSettled(
        otherFilters.map(async (item) => {
          const result = await listAgents(
            {
              keyword: requestedQuery.keyword,
              pageNum: 1,
              pageSize: 1,
              ...(item.value === 'all' ? {} : { status: item.value })
            },
            context.signal
          )
          return { filter: item.value, total: result.total }
        })
      ).then((results) => {
        if (!context.isCurrent()) return
        for (const result of results)
          if (result.status === 'fulfilled') statusCounts[result.value.filter] = result.value.total
      })
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function filterByStatus(value: StatusFilter): void {
  if (statusFilter.value === value) return
  statusFilter.value = value
  query.pageNum = 1
  void load()
}
function search(): void {
  query.pageNum = 1
  void load()
}
function openEditor(agent?: Agent): void {
  if (!canUseCapability('agents', 'read')) {
    if (agent && canUseCapability('agents'))
      void router.push(`/agents/${encodeURIComponent(agent.agentId)}/chat`)
    return
  }
  if (!agent && !canUseCapability('agents', 'create')) return
  toolScope.replace()
  openingId.value = ''
  void router.push(agent ? `/agents/${encodeURIComponent(agent.agentId)}` : '/agents/new')
}
async function openAgentTool(agent: Agent, tool: 'preview' | 'api'): Promise<void> {
  const context = toolScope.replace()
  openingId.value = agent.agentId
  error.value = ''
  try {
    const item = await getAgent(agent.agentId, context.signal)
    if (context.isCurrent()) {
      toolAgent.value = item
      if (tool === 'api') apiOpen.value = true
      else previewOpen.value = true
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) openingId.value = ''
  }
}
watch(
  () => route.query.action,
  (action) => {
    if (action !== 'create') return
    const query = { ...route.query }
    delete query.action
    void router.replace({ path: '/agents/new', query })
  },
  { immediate: true }
)
onMounted(load)
onBeforeUnmount(() => {
  listScope.dispose()
  toolScope.dispose()
  toolAgent.value = null
})
</script>

<style scoped lang="scss">
.agent-management-page {
  display: flex;
  height: 100%;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  color: var(--color-text);
  background: var(--bg-container);
  font-family: var(--workspace-font-family);
}
.agent-toolbar {
  padding: 26px 28px 20px;
}
.agent-toolbar :deep(.workspace-toolbar__title) {
  font-size: 24px;
  font-weight: 600;
}
.agent-toolbar :deep(.workspace-toolbar__subtitle) {
  margin-top: 6px;
  font-size: 13px;
}
.agent-toolbar :deep(.ant-btn-primary) {
  border-radius: 10px;
  box-shadow: 0 3px 8px rgb(97 85 238 / 16%);
}
.agent-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  scrollbar-gutter: stable;
  padding: 0 28px 28px;
}
.agent-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.agent-filter-summary {
  display: flex;
  min-width: 0;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
}
.agent-status-filters {
  display: grid;
  grid-template-columns: 76px 90px 76px;
  gap: 3px;
  padding: 3px;
  border-radius: 10px;
  background: var(--gray-100);
}
.agent-status-filter {
  display: inline-flex;
  min-width: 0;
  min-height: 30px;
  align-items: center;
  gap: 6px;
  justify-content: center;
  padding: 4px 9px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--gray-700);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.agent-status-filter > span {
  min-width: 2ch;
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.agent-status-filter[aria-pressed='true'] {
  background: var(--bg-container);
  color: var(--color-text);
  box-shadow: 0 1px 4px rgb(36 39 51 / 8%);
}
.agent-status-filter[aria-pressed='true'] > span {
  color: var(--main-color);
}
.agent-status-filter:focus-visible,
.create-agent-card:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 3px;
}
.agent-controls :deep(.ant-input-search) {
  width: 240px;
  max-width: 100%;
  margin-left: auto;
}
.agent-controls :deep(.ant-input-affix-wrapper) {
  border-radius: 9px 0 0 9px;
}
.agent-controls :deep(.ant-input-search-button) {
  border-radius: 0 9px 9px 0;
}
.agent-total {
  min-width: 8ch;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.agent-content :deep(.agent-grid) {
  gap: 18px;
}
.agent-card {
  min-height: 208px;
  padding: 18px 18px 12px;
  border-color: var(--gray-200);
  border-radius: 14px;
  box-shadow: 0 1px 2px rgb(36 39 51 / 3%);
}
.agent-card :deep(.resource-card__header) {
  gap: 10px;
}
.agent-card :deep(.resource-card__icon) {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  font-size: 19px;
}
.agent-card :deep(.resource-card__title) {
  flex: 1;
  font-size: 15px;
}
.agent-card :deep(.resource-card__status) {
  padding: 0 7px;
  border-radius: 6px;
  font-size: 11px;
  line-height: 21px;
}
.agent-card :deep(.resource-card__status::before) {
  display: none;
}
.agent-card :deep(.resource-card__status.is-positive) {
  background: #eff8f3;
  color: #287a53;
}
.agent-card :deep(.resource-card__body) {
  min-height: 0;
  margin: 12px 0 10px;
}
.agent-card :deep(.resource-card__footer) {
  min-height: 32px;
  gap: 5px;
  padding-top: 8px;
}
.agent-card :deep(.resource-card__footer-info) {
  color: var(--color-text-tertiary);
  font-size: 11px;
}
.agent-card :deep(.resource-card__actions) {
  gap: 2px;
}
.agent-card :deep(.resource-card-primary) {
  height: 27px;
  padding: 0 8px;
  border-radius: 7px;
  background: var(--main-50);
  font-size: 12px;
}
.agent-card :deep(.agent-card__edit) {
  height: 27px;
  padding: 0 6px;
  color: var(--gray-700);
  font-size: 12px;
}
.agent-card :deep(.workspace-more-menu) {
  width: 22px;
  height: 27px;
  color: var(--color-text-tertiary);
}
.agent-card__description {
  display: -webkit-box;
  min-height: 40px;
  margin: 0 0 12px;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 20px;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.agent-card__metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.agent-card__metadata > span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.create-agent-card {
  display: flex;
  min-height: 208px;
  align-items: center;
  justify-content: center;
  gap: 9px;
  flex-direction: column;
  padding: 22px 16px;
  border: 1px dashed var(--gray-300);
  border-radius: 14px;
  background: transparent;
  color: var(--gray-800);
  font: inherit;
  cursor: pointer;
}
.create-agent-card__icon {
  display: grid;
  width: 40px;
  height: 40px;
  margin-bottom: 3px;
  place-items: center;
  border-radius: 50%;
  background: var(--main-50);
  color: var(--main-700);
  font-size: 18px;
}
.create-agent-card__hint {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.create-agent-card:hover {
  border-color: var(--main-300);
  color: var(--main-700);
  background: var(--main-30);
}
.agent-pending {
  margin: 8px 0 0;
  color: var(--color-warning-700);
  font-size: 12px;
}
.agent-alert {
  margin-bottom: 14px;
}
:deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@container resource-cards (min-width: 1200px) {
  .agent-content :deep(.agent-grid) {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@container resource-cards (min-width: 921px) and (max-width: 1199px) {
  .agent-content :deep(.agent-grid) {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@container resource-cards (min-width: 601px) and (max-width: 920px) {
  .agent-content :deep(.agent-grid) {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@container resource-cards (max-width: 600px) {
  .agent-content :deep(.agent-grid) {
    grid-template-columns: minmax(0, 1fr);
    gap: 14px;
  }
}
@media (max-width: 640px) {
  .agent-toolbar {
    padding: 20px 16px 16px;
  }
  .agent-content {
    padding: 0 16px 24px;
  }
  .agent-controls :deep(.ant-input-search) {
    width: 100%;
  }
  .agent-filter-summary {
    width: 100%;
    justify-content: space-between;
    gap: 8px;
  }
  .agent-status-filter {
    padding-right: 9px;
    padding-left: 9px;
  }
}
</style>
