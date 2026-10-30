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
        <ResourceCardGrid class="agent-grid">
          <ResourceCard
            v-for="agent in agents"
            :key="agent.agentId"
            class="agent-card"
            :title="agent.name"
            :status="agent.status === 'published' ? '已发布' : '草稿'"
            :tone="agent.status === 'published' ? 'positive' : 'neutral'"
            icon-tone="primary"
            interactive
            :open-label="`配置${agent.name}`"
            @open="openEditor(agent)"
          >
            <template #icon><RobotOutlined /></template>
            <template #title-action>
              <button
                class="resource-id-copy"
                type="button"
                aria-label="复制智能体 ID"
                :title="copiedId === agent.agentId ? '已复制 ID' : '复制智能体 ID'"
                @click.stop="copyAgentId(agent)"
              >
                <CheckOutlined v-if="copiedId === agent.agentId" /><CopyOutlined v-else />
              </button>
            </template>
            <p class="agent-card__description" :title="agent.description || '暂无描述'">
              {{ agent.description || '暂无描述' }}
            </p>
            <div class="agent-card__metadata">
              <span><ThunderboltOutlined />{{ agent.skillSlugs.length }} 个技能</span>
              <span><DatabaseOutlined />{{ agent.knowledgeBaseIds.length }} 个知识库</span>
            </div>
            <p v-if="hasUnpublishedAgentChanges(agent)" class="agent-pending">有未发布更改</p>
            <template #footer-info>
              {{
                agent.status === 'published'
                  ? `已发布 v${agent.publishedVersion}`
                  : `草稿 v${agent.version}`
              }}
            </template>
            <template #actions>
              <a-button
                size="small"
                type="text"
                class="resource-card-primary"
                :loading="openingId === agent.agentId"
                aria-label="试聊"
                @click="openAgentTool(agent, 'preview')"
                ><MessageOutlined />试聊</a-button
              >
              <WorkspaceMoreMenu
                :items="agentActions(agent)"
                @command="agentCommand($event, agent)"
              />
            </template>
          </ResourceCard>
          <button class="create-agent-card" type="button" @click="openEditor()">
            <PlusOutlined /><span>创建新的智能体</span>
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
  Pagination as APagination,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  ApiOutlined,
  CheckOutlined,
  CopyOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  MessageOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SettingOutlined
} from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import ResourceCard from '../../../shared/components/ResourceCard.vue'
import ResourceCardGrid from '../../../shared/components/ResourceCardGrid.vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
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
  openingId = ref(''),
  copiedId = ref('')
const listScope = new RequestScope(),
  toolScope = new RequestScope(),
  copyScope = new RequestScope()
function agentActions(agent: Agent): MenuAction[] {
  return [
    { command: 'configure', label: '配置', icon: SettingOutlined },
    ...(agent.status === 'published' ? [{ command: 'api', label: 'API', icon: ApiOutlined }] : []),
    { command: 'copy', label: '复制 ID', icon: CopyOutlined }
  ]
}
function agentCommand(command: string, agent: Agent): void {
  if (command === 'configure') openEditor(agent)
  else if (command === 'api' && agent.status === 'published') void openAgentTool(agent, 'api')
  else if (command === 'copy') void copyAgentId(agent)
}
async function copyAgentId(agent: Agent): Promise<void> {
  const context = copyScope.replace()
  copiedId.value = ''
  error.value = ''
  try {
    if (!navigator.clipboard?.writeText)
      throw new Error('当前浏览器无法复制，请允许剪贴板访问后重试')
    await navigator.clipboard.writeText(agent.agentId)
    if (context.isCurrent()) copiedId.value = agent.agentId
  } catch (reason) {
    if (context.isCurrent()) error.value = errorMessage(reason)
  }
}
async function load(): Promise<void> {
  copyScope.replace()
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
  copyScope.replace()
  toolScope.replace()
  openingId.value = ''
  void router.push(agent ? `/agents/${encodeURIComponent(agent.agentId)}` : '/agents/new')
}
async function openAgentTool(agent: Agent, tool: 'preview' | 'api'): Promise<void> {
  copyScope.replace()
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
  copyScope.dispose()
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
  min-height: 208px;
  align-items: center;
  justify-content: center;
  gap: 16px;
  flex-direction: column;
  border: 1px dashed var(--gray-150);
  border-radius: 10px;
  background: #ffffff70;
  color: var(--color-text-secondary);
  font: inherit;
  cursor: pointer;
}
.create-agent-card > .anticon {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border-radius: 50%;
  background: var(--main-50);
  color: var(--main-700);
  font-size: 20px;
}
.create-agent-card:hover {
  border-color: var(--main-300);
  color: var(--main-700);
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
@media (max-width: 640px) {
  .agent-content {
    padding: 16px;
  }
}
</style>
