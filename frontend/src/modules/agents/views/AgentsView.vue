<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <main class="app-container agent-management-page">
    <WorkspaceToolbar title="智能体">
      <template #actions>
        <a-tooltip title="刷新智能体">
          <a-button :loading="loading" aria-label="刷新智能体" @click="load"
            ><template #icon><ReloadOutlined /></template
          ></a-button>
        </a-tooltip>
        <a-button type="primary" aria-label="新建智能体" @click="openEditor()"
          ><template #icon><PlusOutlined /></template>新建智能体</a-button
        >
      </template>
    </WorkspaceToolbar>
    <section class="agent-content">
      <div class="agent-controls">
        <a-input-search
          v-model:value="query.keyword"
          :maxlength="100"
          allow-clear
          placeholder="搜索智能体"
          @search="search"
        />
        <span class="agent-total">共 {{ total }} 个</span>
      </div>
      <WorkspaceState v-if="loading && !agents.length" kind="loading" />
      <WorkspaceState
        v-else-if="error && !agents.length"
        kind="error"
        :message="error"
        @retry="load"
      />
      <WorkspaceState v-else-if="!agents.length" kind="empty" title="暂无智能体" />
      <template v-else>
        <a-alert v-if="error" class="agent-alert" type="warning" :message="error" show-icon />
        <div class="agent-grid">
          <article v-for="agent in agents" :key="agent.agentId" class="agent-card">
            <div class="agent-card__top">
              <span class="agent-card__icon"><RobotOutlined /></span>
            </div>
            <div class="agent-card__header">
              <strong :title="agent.name">{{ agent.name }}</strong>
              <a-tag :color="agent.status === 'published' ? 'green' : 'default'">{{
                agent.status === 'published' ? '已发布' : '草稿'
              }}</a-tag>
            </div>
            <p>{{ agent.description || '暂无描述' }}</p>
            <div class="agent-card__metadata">
              <span><ThunderboltOutlined />{{ agent.skillSlugs.length }} 个技能</span>
              <span><DatabaseOutlined />{{ agent.knowledgeBaseIds.length }} 个知识库</span>
              <span v-if="hasUnpublishedAgentChanges(agent)" class="agent-pending"
                >有未发布更改</span
              >
              <span v-else>v{{ agent.version }}</span>
            </div>
            <div class="agent-card__footer">
              <a-button size="small" type="link" aria-label="配置" @click="openEditor(agent)"
                ><SettingOutlined />配置</a-button
              >
              <a-button
                size="small"
                type="link"
                :loading="openingId === agent.agentId"
                aria-label="试聊"
                @click="openAgentTool(agent, 'preview')"
                ><MessageOutlined />试聊</a-button
              >
              <a-button
                v-if="agent.status === 'published'"
                size="small"
                type="link"
                aria-label="API"
                @click="openAgentTool(agent, 'api')"
                ><ApiOutlined />API</a-button
              >
              <a-button type="text" :aria-label="`编辑${agent.name}`" @click="openEditor(agent)"
                ><EllipsisOutlined
              /></a-button>
            </div>
          </article>
          <button class="create-agent-card" type="button" @click="openEditor()">
            <PlusOutlined /><span>创建新的智能体</span>
          </button>
        </div>
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
  Pagination as APagination,
  Tag as ATag,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  ApiOutlined,
  DatabaseOutlined,
  EllipsisOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getAgent, listAgents } from '../api/index.ts'
import type { Agent } from '../api/index.ts'
import { hasUnpublishedAgentChanges } from '../presentation.ts'
import AgentPreviewDrawer from '../components/AgentPreviewDrawer.vue'
import AgentApiDrawer from '../components/AgentApiDrawer.vue'
const agents = ref<Agent[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const query = reactive({ keyword: '', pageNum: 1, pageSize: 12 })
const route = useRoute(),
  router = useRouter()
const previewOpen = ref(false),
  apiOpen = ref(false),
  toolAgent = ref<Agent | null>(null),
  openingId = ref('')
const listScope = new RequestScope(),
  toolScope = new RequestScope()
async function load(): Promise<void> {
  const context = listScope.replace()
  loading.value = true
  error.value = ''
  try {
    const page = await listAgents(query, context.signal)
    if (!context.isCurrent()) return
    agents.value = page.rows
    total.value = page.total
    if (!page.rows.length && query.pageNum > 1) {
      query.pageNum -= 1
      await load()
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function search(): void {
  query.pageNum = 1
  void load()
}
function openEditor(agent?: Agent): void {
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
  background: var(--bg-page);
  font-family: var(--workspace-font-family);
}
.agent-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 24px 28px 32px;
}
.agent-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.agent-controls :deep(.ant-input-search) {
  width: min(360px, 70%);
}
.agent-total {
  margin-left: auto;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.agent-grid {
  display: flex;
  flex-direction: column;
}
.agent-card {
  display: grid;
  min-width: 0;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  column-gap: 16px;
  padding: 22px 0;
  border-bottom: 1px solid var(--gray-150);
}
.agent-card__top {
  grid-column: 1;
  grid-row: 1 / 4;
}
.agent-card__header {
  grid-column: 2;
  display: flex;
  align-items: center;
  gap: 10px;
}
.agent-card__header strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  font-size: 16px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-card__header :deep(.ant-tag) {
  margin: 0;
}
.agent-card__icon {
  display: grid;
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  place-items: center;
  color: var(--main-700);
  background: var(--main-50);
  border-radius: 7px;
  font-size: 18px;
}
.agent-card p {
  grid-column: 2;
  display: -webkit-box;
  margin: 7px 0 10px;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.7;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.agent-card__metadata {
  grid-column: 2;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
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
  min-height: 64px;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  padding: 12px 0;
  border: 0;
  background: transparent;
  color: var(--main-700);
  font: inherit;
  cursor: pointer;
}
.create-agent-card > .anticon {
  width: 42px;
  font-size: 18px;
}
.create-agent-card:hover {
  color: var(--main-500);
}
.create-agent-card:focus-visible {
  outline: 2px solid var(--main-400);
  outline-offset: 2px;
}
.agent-pending {
  color: var(--color-warning-700);
}
.agent-card__footer {
  grid-column: 3;
  grid-row: 1 / 4;
  align-self: center;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 4px;
}
.agent-alert {
  margin-bottom: 14px;
}
:deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (max-width: 900px) {
  .agent-card__footer {
    grid-column: 2;
    grid-row: 4;
    margin-top: 12px;
  }
}
@media (max-width: 640px) {
  .agent-content {
    padding: 16px;
  }
  .agent-card {
    grid-template-columns: 36px minmax(0, 1fr);
    column-gap: 12px;
    padding-block: 18px;
  }
  .agent-card__icon {
    width: 36px;
    height: 36px;
    flex-basis: 36px;
  }
}
:deep(.ant-btn-link) {
  color: var(--main-700);
}
</style>
