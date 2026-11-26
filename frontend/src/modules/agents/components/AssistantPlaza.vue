<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Button as AButton,
  InputSearch as AInputSearch,
  Pagination as APagination
} from 'ant-design-vue'
import { MessageOutlined } from '@ant-design/icons-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import ResourceCard from '../../../shared/components/ResourceCard.vue'
import ResourceCardGrid from '../../../shared/components/ResourceCardGrid.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import { canUseCapability, identity } from '../../identity/public.ts'
import { listPlazaAssistants } from '../api/index.ts'
import type { PlazaAssistant } from '../api/index.ts'
import AgentIdentityIcon from './AgentIdentityIcon.vue'

const router = useRouter()
const emit = defineEmits<{
  loading: [value: boolean]
  unpublish: [assistant: PlazaAssistant]
}>()
const moderationActions = [{ command: 'unpublish', label: '紧急下架', danger: true }]
const scope = new RequestScope()
const keyword = ref('')
const search = ref('')
const page = ref(1)
const pageSize = 12
const total = ref(0)
const rows = ref<PlazaAssistant[]>([])
const loading = ref(true)
const error = ref('')

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

function openChat(agentId: string): void {
  void router.push({
    name: 'assistant-plaza-chat',
    params: { id: agentId }
  })
}

watch(page, load)
watch(loading, (value) => emit('loading', value), { immediate: true })
onMounted(refresh)
onBeforeUnmount(() => scope.dispose())
defineExpose({ refresh })
</script>

<template>
  <section aria-label="助手广场" :aria-busy="loading">
    <div class="plaza-controls">
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
  </section>
</template>

<style scoped>
.plaza-controls {
  display: flex;
  justify-content: flex-end;
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
@media (max-width: 640px) {
  .plaza-controls :deep(.ant-input-search) {
    width: 100%;
  }
}
.plaza-description {
  margin: 0;
  color: var(--workspace-text-secondary);
  overflow-wrap: anywhere;
}
</style>
