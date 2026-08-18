<template>
  <section class="chat-session-panel">
    <div class="sessions-header">
      <div class="sessions-heading">
        <span class="sessions-kicker">工作台</span>
        <strong>对话</strong>
      </div>
      <el-button type="primary" :icon="Plus" aria-label="新建对话" @click="emit('new')">新建</el-button>
    </div>
    <div class="session-tools">
      <el-input
        :model-value="query"
        clearable
        :prefix-icon="Search"
        placeholder="搜索会话"
        aria-label="搜索会话"
        @update:model-value="emit('update:query', $event)"
      />
    </div>
    <div v-loading="loading" class="session-list">
      <div
        v-for="session in sessions"
        :key="session.sessionId"
        class="session-item"
        :class="{ active: session.sessionId === activeSessionId }"
      >
        <button type="button" class="session-copy" @click="emit('open', session.sessionId)">
          <strong>{{ session.title }}</strong>
          <small>{{ formatTime(session.updatedAt || session.createdAt) }}</small>
        </button>
        <span class="session-actions" @click.stop @keydown.stop>
          <WorkspaceMoreMenu :items="[{ command: 'archive', label: '归档会话', icon: Delete }]" @command="emit('archive', session.sessionId)" />
        </span>
      </div>
      <el-empty v-if="!loading && sessions.length === 0" description="暂无匹配会话" />
    </div>
  </section>
</template>

<script setup>
import { Delete, Plus, Search } from '@element-plus/icons-vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'

defineProps({
  sessions: { type: Array, default: () => [] },
  activeSessionId: { type: String, default: null },
  query: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:query', 'new', 'open', 'archive'])

function formatTime(value) {
  if (!value) return '刚刚'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '刚刚' : parsed.toLocaleString()
}
</script>

<style scoped lang="scss">
.chat-session-panel { height: 100%; min-height: 0; display: flex; flex-direction: column; }
.sessions-header { min-height: 72px; display: flex; align-items: center; justify-content: space-between; padding: 0 14px; border-bottom: 1px solid var(--workspace-border); }
.sessions-heading { display: grid; gap: 2px; }
.sessions-kicker { color: var(--workspace-text-muted); font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; }
.sessions-heading strong { color: var(--workspace-text); font-size: 16px; }
.session-tools { padding: 10px; border-bottom: 1px solid var(--workspace-border); }
.session-list { flex: 1; overflow: auto; padding: 8px; }
.session-item { width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px; margin-bottom: 4px; border: 1px solid transparent; border-radius: 6px; color: inherit; background: transparent; text-align: left; cursor: pointer; }
.session-item:hover,
.session-item.active { background: var(--workspace-main-soft); }
.session-item.active { color: var(--workspace-main-deep); }
.session-item :deep(.workspace-more-menu) { opacity: 0; }
.session-item:hover :deep(.workspace-more-menu),
.session-item.active :deep(.workspace-more-menu),
.session-item:focus-within :deep(.workspace-more-menu) { opacity: 1; }
.session-copy { min-width: 0; flex: 1; display: grid; gap: 4px; padding: 0; border: 0; color: inherit; background: transparent; font: inherit; text-align: left; cursor: pointer; }
.session-copy strong,
.session-copy small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-copy small { color: var(--el-text-color-secondary); font-size: 12px; }
@media (hover: none) {
  .session-item :deep(.workspace-more-menu) { opacity: 1; }
}
</style>
