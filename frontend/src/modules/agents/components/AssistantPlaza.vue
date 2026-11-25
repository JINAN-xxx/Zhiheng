<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Button as AButton,
  InputSearch as AInputSearch,
  Pagination as APagination,
  Spin as ASpin
} from 'ant-design-vue'
import { HistoryOutlined, MessageOutlined } from '@ant-design/icons-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import HistoryDialog from '../../../shared/components/HistoryDialog.vue'
import HistoryRecord from '../../../shared/components/HistoryRecord.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceCard from '../../../shared/components/ResourceCard.vue'
import ResourceCardGrid from '../../../shared/components/ResourceCardGrid.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import { canUseCapability, identity } from '../../identity/public.ts'
import { listPlazaAssistants } from '../api/index.ts'
import type { PlazaAssistant } from '../api/index.ts'
import { getPlazaHistory } from '../api/conversations.ts'
import type { AssistantConversation } from '../api/conversations.ts'
import AgentIdentityIcon from './AgentIdentityIcon.vue'

const router = useRouter()
const emit = defineEmits<{
  loading: [value: boolean]
  unpublish: [assistant: PlazaAssistant]
}>()
const moderationActions = [{ command: 'unpublish', label: '紧急下架', danger: true }]
const scope = new RequestScope()
const historyScope = new RequestScope()
const keyword = ref('')
const search = ref('')
const page = ref(1)
const pageSize = 12
const total = ref(0)
const rows = ref<PlazaAssistant[]>([])
const history = ref<AssistantConversation[]>([])
const loading = ref(true)
const error = ref('')
const historyError = ref('')
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyMore = ref(false)
const historyLoaded = ref(false)
const historyPageSize = 12

async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await listPlazaAssistants(
      { keyword: search.value, page: page.value, pageSize },
      context.signal
    )
    if (!context.isCurrent()) return
    rows.value = result.rows
    total.value = result.total
    page.value = Math.min(page.value, Math.max(1, Math.ceil(result.total / pageSize)))
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}

async function loadHistory(append = false): Promise<void> {
  const before = append ? history.value.at(-1)?.id : undefined
  if (append && (!before || historyLoading.value)) return
  const context = historyScope.replace()
  historyError.value = ''
  historyLoading.value = true
  try {
    const result = await getPlazaHistory(context.signal, before)
    if (!context.isCurrent()) return
    history.value = append ? [...history.value, ...result] : result
    historyMore.value = result.length === historyPageSize
    historyLoaded.value = true
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) historyError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) historyLoading.value = false
  }
}

function openHistory(): void {
  historyOpen.value = true
  if (!historyLoaded.value && !historyLoading.value) void loadHistory()
}

function refresh(): void {
  void load()
}

function applySearch(): void {
  if (page.value === 1) {
    search.value = keyword.value.trim()
    void load()
  } else {
    search.value = keyword.value.trim()
    page.value = 1
  }
}

function openChat(agentId: string, conversation?: string): void {
  historyOpen.value = false
  void router.push({
    name: 'assistant-plaza-chat',
    params: { id: agentId },
    query: conversation ? { conversation } : {}
  })
}

watch(page, load)
watch(loading, (value) => emit('loading', value), { immediate: true })
onMounted(refresh)
onBeforeUnmount(() => {
  scope.dispose()
  historyScope.dispose()
})
defineExpose({ refresh })
</script>

<template>
  <section aria-label="助手广场" :aria-busy="loading">
    <div class="plaza-controls">
      <button
        type="button"
        class="plaza-history-trigger"
        aria-haspopup="dialog"
        aria-controls="plaza-history-dialog"
        :aria-expanded="historyOpen"
        @click="openHistory"
      >
        <HistoryOutlined />历史记录
      </button>
      <a-input-search
        v-model:value="keyword"
        allow-clear
        :maxlength="100"
        placeholder="搜索广场助手"
        aria-label="搜索广场助手"
        @search="applySearch"
      />
    </div>
    <WorkspaceState v-if="loading && !rows.length" kind="loading" />
    <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="load" />
    <WorkspaceState
      v-else-if="!rows.length"
      kind="empty"
      :title="search ? '没有匹配的助手' : '暂无上架助手'"
    />
    <ResourceCardGrid v-else>
      <ResourceCard v-for="assistant in rows" :key="assistant.agentId" :title="assistant.name">
        <template #icon><AgentIdentityIcon /></template>
        <p v-if="assistant.description" class="plaza-description">{{ assistant.description }}</p>
        <template #actions>
          <a-button
            v-if="assistant.canStart !== false && canUseCapability('agents', 'use')"
            type="text"
            size="small"
            class="resource-card-primary"
            :aria-label="`使用${assistant.name}`"
            @click="openChat(assistant.agentId)"
            ><MessageOutlined />开始对话</a-button
          >
          <WorkspaceMoreMenu
            v-if="identity?.is_admin && assistant.plazaVersion"
            :items="moderationActions"
            @command="emit('unpublish', assistant)"
          />
        </template>
      </ResourceCard>
    </ResourceCardGrid>
    <a-pagination
      v-if="total > pageSize"
      v-model:current="page"
      :page-size="pageSize"
      :total="total"
      :show-size-changer="false"
    />
    <HistoryDialog v-model:open="historyOpen" title="广场历史记录" dialog-id="plaza-history-dialog">
      <section class="history-list" aria-label="广场历史对话">
        <div class="history-list__heading">
          <span>我的会话</span>
          <RefreshButton label="刷新广场历史" :loading="historyLoading" @click="loadHistory()" />
        </div>
        <p v-if="historyError" class="history-list__error" role="alert">{{ historyError }}</p>
        <ASpin :spinning="historyLoading" wrapper-class-name="history-list__loading">
          <HistoryRecord
            v-for="item in history"
            :key="item.id"
            :title="item.title"
            :timestamp="item.updatedAt"
            :open-label="`${item.name} · ${item.title}`"
            @open="openChat(item.agentId, item.id)"
          >
            <template #meta
              ><span>{{ item.name }}</span></template
            >
          </HistoryRecord>
          <WorkspaceState
            v-if="!historyLoading && !historyError && !history.length"
            kind="empty"
            title="暂无会话"
          />
        </ASpin>
        <AButton
          v-if="historyMore"
          class="history-list__pagination"
          type="text"
          :disabled="historyLoading"
          @click="loadHistory(true)"
          >加载更多会话</AButton
        >
      </section>
    </HistoryDialog>
  </section>
</template>

<style scoped>
.plaza-controls {
  display: flex;
  min-height: 36px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}
.plaza-controls :deep(.ant-input-search) {
  width: 240px;
  max-width: 100%;
}
.plaza-controls :deep(.ant-input-affix-wrapper) {
  border-radius: 9px 0 0 9px;
}
.plaza-controls :deep(.ant-input-search-button) {
  border-radius: 0 9px 9px 0;
}
.plaza-history-trigger {
  display: inline-flex;
  min-height: 32px;
  align-items: center;
  gap: 6px;
  padding-inline: 12px;
  border: 1px solid var(--workspace-border);
  border-radius: var(--workspace-radius-sm);
  background: var(--workspace-surface);
  color: var(--workspace-text);
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
}
.plaza-history-trigger:hover,
.plaza-history-trigger:focus-visible {
  color: var(--workspace-main);
  border-color: var(--workspace-main);
}
@media (max-width: 640px) {
  .plaza-controls {
    min-height: 0;
    flex-wrap: wrap;
  }
  .plaza-controls :deep(.ant-input-search) {
    order: -1;
    width: 100%;
  }
}
.plaza-description {
  margin: 0;
  color: var(--workspace-text-secondary);
  overflow-wrap: anywhere;
}
</style>
