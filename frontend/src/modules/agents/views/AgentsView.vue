<!-- Adapted from original agent UI, MIT licensed. -->
<template>
  <main class="app-container agent-management-page">
    <WorkspaceToolbar title="智能体">
      <template #actions>
        <a-tooltip title="刷新智能体">
          <a-button shape="circle" :loading="loading" aria-label="刷新智能体" @click="load"
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
            <div class="agent-card__header">
              <span class="agent-card__icon"><RobotOutlined /></span>
              <strong :title="agent.name">{{ agent.name }}</strong>
              <a-tag :color="agent.status === 'published' ? 'green' : 'default'">{{
                agent.status === 'published' ? '已发布' : '草稿'
              }}</a-tag>
            </div>
            <p>{{ agent.description || '暂无描述' }}</p>
            <div class="agent-card__metadata">
              <span>{{ agent.skillSlugs.length }} 个技能</span>
              <span>{{ agent.knowledgeBaseIds.length }} 个知识库</span>
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
            </div>
          </article>
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
    <AgentEditorModal
      v-model="editorOpen"
      :agent-id="selectedAgentId"
      @changed="load"
      @deleted="load"
      @preview="showPreview"
    />
    <AgentPreviewDrawer v-model="previewOpen" :agent="toolAgent" />
    <AgentApiDrawer v-model="apiOpen" :agent="toolAgent" />
  </main>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
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
import AgentEditorModal from '../components/AgentEditorModal.vue'
import AgentPreviewDrawer from '../components/AgentPreviewDrawer.vue'
import AgentApiDrawer from '../components/AgentApiDrawer.vue'
const agents = ref<Agent[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const query = reactive({ keyword: '', pageNum: 1, pageSize: 12 })
const editorOpen = ref(false),
  selectedAgentId = ref(''),
  previewOpen = ref(false),
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
  selectedAgentId.value = agent?.agentId ?? ''
  editorOpen.value = true
}
function showPreview(agent: Agent): void {
  editorOpen.value = false
  toolAgent.value = agent
  previewOpen.value = true
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
.agent-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 20px 24px 28px;
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
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.agent-card {
  display: flex;
  min-width: 0;
  min-height: 190px;
  flex-direction: column;
  padding: 17px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.agent-card__header {
  display: flex;
  align-items: center;
  gap: 10px;
}
.agent-card__header strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-card__header :deep(.ant-tag) {
  margin: 0;
}
.agent-card__icon {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  place-items: center;
  color: var(--main-700);
  background: var(--main-50);
  border-radius: 7px;
  font-size: 18px;
}
.agent-card p {
  display: -webkit-box;
  min-height: 42px;
  margin: 14px 0 12px;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 13px;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.agent-card__metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-bottom: 12px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.agent-pending {
  color: var(--color-warning-700);
}
.agent-card__footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 5px;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--gray-100);
}
.agent-alert {
  margin-bottom: 14px;
}
:deep(.ant-pagination) {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (max-width: 1100px) {
  .agent-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 640px) {
  .agent-content {
    padding: 16px;
  }
  .agent-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
