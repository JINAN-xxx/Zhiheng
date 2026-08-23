<template>
  <section class="recent-sessions" aria-label="最近会话">
    <div class="recent-sessions__heading">
      <span>最近会话</span>
      <button type="button" class="recent-sessions__refresh" aria-label="刷新最近会话" @click="emit('refresh')">
        <ReloadOutlined />
      </button>
    </div>

    <a-input
      v-model:value="query"
      allow-clear
      placeholder="搜索会话"
      aria-label="搜索最近会话"
    >
      <template #prefix><SearchOutlined /></template>
    </a-input>

    <p v-if="error && sessions.length" class="recent-sessions__stale" role="status">
      刷新失败，列表可能已过期
    </p>
    <p v-else-if="error" class="recent-sessions__error" role="alert">{{ error }}</p>

    <a-spin :spinning="loading" class="recent-sessions__loading">
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
        <WorkspaceMoreMenu
          :items="archiveActions"
          @command="emit('archive-session', session.sessionId)"
        />
      </div>
      <a-empty
        v-if="!loading && filteredSessions.length === 0"
        :description="query ? '没有匹配会话' : '暂无会话'"
      />
      </div>
    </a-spin>
  </section>
</template>

<script setup>
import { DeleteOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeSessionId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['archive-session', 'open-session', 'refresh'])
const query = ref('')
const archiveActions = Object.freeze([{ command: 'archive', label: '归档会话', icon: DeleteOutlined }])

const filteredSessions = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  if (!keyword) return props.sessions
  return props.sessions.filter((session) => session.title.toLocaleLowerCase().includes(keyword))
})

function formatTime(value) {
  if (!value) return '刚刚'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '刚刚' : parsed.toLocaleString()
}
</script>

<style scoped lang="scss">
.recent-sessions { min-height: 0; display: flex; flex: 1; flex-direction: column; gap: 9px; padding: 13px 10px; color: var(--workspace-sidebar-text); }
.recent-sessions__heading { display: flex; min-height: 28px; align-items: center; justify-content: space-between; color: var(--workspace-sidebar-muted); font-size: 12px; font-weight: 600; }
.recent-sessions__refresh { display: inline-flex; width: 30px; height: 30px; align-items: center; justify-content: center; border: 0; background: transparent; color: inherit; cursor: pointer; }
.recent-sessions__stale,
.recent-sessions__error { margin: 0; padding: 8px; border-radius: var(--workspace-radius-sm); font-size: 12px; }
.recent-sessions__stale { color: var(--workspace-warning); background: var(--workspace-warning-soft); }
.recent-sessions__error { color: var(--workspace-danger); background: var(--workspace-danger-soft); }
.recent-sessions__list { min-height: 0; flex: 1; overflow-y: auto; }
.recent-sessions__loading { min-height: 0; flex: 1; overflow: hidden; }
.recent-sessions__loading :deep(.ant-spin-container) { height: 100%; }
.recent-session { display: flex; align-items: center; gap: 4px; margin-bottom: 3px; padding: 7px 6px 7px 9px; border-radius: var(--workspace-radius-sm); }
.recent-session:hover { background: var(--workspace-sidebar-surface); }
.recent-session.active { background: var(--workspace-main); color: #fff; }
.recent-session__open { min-width: 0; display: grid; flex: 1; gap: 3px; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.recent-session__open strong,
.recent-session__open small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-session__open strong { font-size: 13px; font-weight: 500; }
.recent-session__open small { color: var(--workspace-sidebar-muted); font-size: 11px; }
.recent-sessions :deep(.ant-input-affix-wrapper) { border-color: var(--workspace-sidebar-border); background: var(--workspace-sidebar-surface); box-shadow: none; }
.recent-sessions :deep(.ant-input-affix-wrapper input),
.recent-sessions :deep(.ant-input-prefix),
.recent-sessions :deep(.ant-input-clear-icon) { color: var(--workspace-sidebar-text); background: transparent; }
.recent-sessions :deep(.ant-empty-description) { color: var(--workspace-sidebar-muted); }
.recent-session :deep(.workspace-more-menu) { opacity: 0; }
.recent-session:hover :deep(.workspace-more-menu),
.recent-session:focus-within :deep(.workspace-more-menu) { opacity: 1; }
@media (hover: none) { .recent-session :deep(.workspace-more-menu) { opacity: 1; } }
</style>
