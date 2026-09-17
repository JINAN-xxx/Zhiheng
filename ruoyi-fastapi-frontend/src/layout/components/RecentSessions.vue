<template>
  <section class="recent-sessions" aria-label="对话列表">
    <div class="recent-sessions__heading">
      <a-radio-group :value="status" size="small" aria-label="会话状态" @update:value="emit('status-change', $event)">
        <a-radio-button value="active">最近会话</a-radio-button>
        <a-radio-button value="archived">已归档</a-radio-button>
      </a-radio-group>
      <button type="button" class="recent-sessions__refresh" aria-label="刷新会话列表" @click="emit('refresh')">
        <ReloadOutlined />
      </button>
    </div>

    <a-input
      v-model:value="query"
      allow-clear
      placeholder="搜索当前页会话"
      :aria-label="status === 'archived' ? '搜索已归档会话' : '搜索最近会话'"
    >
      <template #prefix><SearchOutlined /></template>
    </a-input>

    <p v-if="error && sessions.length" class="recent-sessions__stale" role="status">
      刷新失败，列表可能已过期
    </p>
    <p v-else-if="error" class="recent-sessions__error" role="alert">{{ error }}</p>

    <a-spin :spinning="loading" wrapper-class-name="recent-sessions__loading">
      <div class="recent-sessions__list">
      <div
        v-for="session in filteredSessions"
        :key="session.sessionId"
        class="recent-session"
        :class="{ active: session.sessionId === activeSessionId }"
      >
        <button
          type="button"
          class="recent-session__open"
          :aria-current="session.sessionId === activeSessionId ? 'page' : undefined"
          @click="emit('open-session', session.sessionId)"
        >
          <strong>{{ session.title }}</strong>
          <small>{{ formatTime(session.updatedAt) }}</small>
        </button>
        <a-spin :spinning="mutatingIds.includes(session.sessionId)" size="small">
          <WorkspaceMoreMenu
            :items="sessionActions"
            @command="handleCommand($event, session.sessionId)"
          />
        </a-spin>
      </div>
      <a-empty
        v-if="!loading && filteredSessions.length === 0"
        :description="query ? '当前页没有匹配会话' : status === 'archived' ? '暂无已归档会话' : '暂无会话'"
      />
      </div>
    </a-spin>
    <a-pagination
      v-if="total > pageSize"
      class="recent-sessions__pagination"
      :current="pageNum"
      :page-size="pageSize"
      :total="total"
      :show-size-changer="false"
      size="small"
      simple
      @change="emit('page-change', $event)"
    />
  </section>
</template>

<script setup>
import { DeleteOutlined, EditOutlined, InboxOutlined, ReloadOutlined, RollbackOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { computed, ref, watch } from 'vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeSessionId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  status: { type: String, default: 'active' },
  pageNum: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  total: { type: Number, default: 0 },
  mutatingIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['delete-session', 'open-session', 'refresh', 'rename-session', 'archive-session', 'restore-session', 'status-change', 'page-change'])
const query = ref('')
const sessionActions = computed(() => [
  { command: 'rename', label: '重命名', icon: EditOutlined },
  props.status === 'archived'
    ? { command: 'restore', label: '恢复对话', icon: RollbackOutlined }
    : { command: 'archive', label: '归档会话', icon: InboxOutlined },
  { command: 'delete', label: '删除会话', icon: DeleteOutlined, danger: true, divided: true },
])

function handleCommand(command, sessionId) {
  if (props.mutatingIds.includes(sessionId)) return
  if (command === 'rename') emit('rename-session', sessionId)
  if (command === 'delete') emit('delete-session', sessionId)
  if (command === 'archive') emit('archive-session', sessionId)
  if (command === 'restore') emit('restore-session', sessionId)
}

watch(() => props.status, () => { query.value = '' })

const filteredSessions = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  if (!keyword) return props.sessions
  return props.sessions.filter((session) => session.title.toLocaleLowerCase().includes(keyword))
})

function formatTime(value) {
  if (!value) return '刚刚'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '刚刚' : new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(parsed)
}
</script>

<style scoped lang="scss">
.recent-sessions { min-height: 0; display: flex; flex: 1; flex-direction: column; gap: 9px; padding: 13px 10px; color: var(--color-text); }
.recent-sessions__heading { display: flex; min-height: 28px; align-items: center; justify-content: space-between; color: var(--color-text-secondary); font-size: 12px; font-weight: 600; }
.recent-sessions__heading :deep(.ant-radio-button-wrapper) { padding-inline: 9px; font-size: 12px; }
.recent-sessions__pagination { align-self: center; }
.recent-sessions__refresh { display: inline-flex; width: 30px; height: 30px; align-items: center; justify-content: center; border: 0; background: transparent; color: inherit; cursor: pointer; }
.recent-sessions__stale,
.recent-sessions__error { margin: 0; padding: 8px; border-radius: var(--workspace-radius-sm); font-size: 12px; }
.recent-sessions__stale { color: var(--workspace-warning); background: var(--workspace-warning-soft); }
.recent-sessions__error { color: var(--workspace-danger); background: var(--workspace-danger-soft); }
.recent-sessions__list { min-height: 0; height: 100%; flex: 1; overflow-y: auto; }
.recent-sessions__loading { min-height: 0; flex: 1; overflow: hidden; }
.recent-sessions__loading :deep(.ant-spin-container) { height: 100%; }
.recent-session { display: flex; align-items: center; gap: 4px; margin-bottom: 3px; padding: 7px 6px 7px 9px; border-radius: var(--workspace-radius-sm); }
.recent-session:hover { background: var(--gray-50); }
.recent-session.active { background: var(--main-50); color: var(--main-700); }
.recent-session__open { min-width: 0; display: grid; flex: 1; gap: 3px; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.recent-session__open strong,
.recent-session__open small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-session__open strong { font-size: 13px; font-weight: 500; }
.recent-session__open small { color: var(--color-text-secondary); font-size: 11px; }
.recent-session.active .recent-session__open small { color: var(--color-text-secondary); }
.recent-sessions :deep(.ant-input-affix-wrapper) { border-color: var(--gray-150); background: var(--bg-container); box-shadow: none; }
.recent-sessions :deep(.ant-input-affix-wrapper input),
.recent-sessions :deep(.ant-input-prefix),
.recent-sessions :deep(.ant-input-clear-icon) { color: var(--color-text); background: transparent; }
.recent-sessions :deep(.ant-empty-description) { color: var(--color-text-secondary); }
.recent-session :deep(.workspace-more-menu) { opacity: 0; }
.recent-session:hover :deep(.workspace-more-menu),
.recent-session:focus-within :deep(.workspace-more-menu) { opacity: 1; }
@media (hover: none) { .recent-session :deep(.workspace-more-menu) { opacity: 1; } }
</style>
