<template>
  <section class="recent-work" aria-label="最近工作">
    <div class="recent-work__heading">
      <span>最近工作</span>
      <a-tooltip title="刷新工作列表"><a-button type="text" size="small" aria-label="刷新工作列表" @click="emitFilters"><ReloadOutlined /></a-button></a-tooltip>
    </div>
    <a-input v-model:value="keyword" allow-clear placeholder="搜索工作" aria-label="搜索最近工作" @press-enter="emitFilters">
      <template #prefix><SearchOutlined /></template>
    </a-input>
    <a-select v-model:value="status" aria-label="筛选工作状态" :options="statusOptions" @change="emitFilters" />
    <p v-if="error && tasks.length" class="recent-work__stale" role="status">刷新失败，列表可能已过期</p>
    <p v-else-if="error" class="recent-work__error" role="alert">{{ error }}</p>
    <a-spin :spinning="loading" class="recent-work__loading">
      <div class="recent-work__list">
        <div v-for="task in tasks" :key="task.taskId" class="recent-work__item" :class="{ active: task.taskId === activeTaskId }">
          <button type="button" class="recent-work__open" :disabled="!canOpen" @click="canOpen && emit('open', task.taskId)">
            <strong>{{ task.title }}</strong>
            <small>{{ statusLabel(task.status) }} · {{ formatTime(task.updatedAt) }}</small>
          </button>
          <WorkspaceMoreMenu v-if="taskActions(task).length" :items="taskActions(task)" @command="emit($event, task)" />
        </div>
        <a-empty v-if="!loading && !tasks.length" description="暂无工作" />
      </div>
    </a-spin>
    <a-pagination
      v-if="total > pageSize"
      simple
      size="small"
      :current="pageNum"
      :page-size="pageSize"
      :total="total"
      @change="(page) => emit('filter', { keyword: keyword.trim(), status, pageNum: page, pageSize })"
    />
  </section>
</template>

<script setup>
import { DeleteOutlined, EditOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import WorkspaceMoreMenu from '@/components/Workspace/WorkspaceMoreMenu.vue'
const props = defineProps({
  tasks: { type: Array, default: () => [] }, activeTaskId: { type: String, default: '' }, loading: Boolean,
  error: { type: String, default: '' }, total: { type: Number, default: 0 }, pageNum: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 }, canOpen: Boolean, canEdit: Boolean, canRemove: Boolean,
  keyword: { type: String, default: '' }, status: { type: String, default: '' },
})
const emit = defineEmits(['delete', 'filter', 'open', 'rename'])
const keyword = ref(props.keyword)
const status = ref(props.status)
const statusOptions = [
  { value: '', label: '未归档' }, { value: 'active', label: '进行中' },
  { value: 'completed', label: '已完成' }, { value: 'archived', label: '已归档' },
]
const taskActions = (task) => [
  props.canEdit && task.status === 'active' && !task.currentRunId
    && { command: 'rename', label: '重命名', icon: EditOutlined },
  props.canRemove && !task.currentRunId
    && { command: 'delete', label: '删除工作', icon: DeleteOutlined, danger: true, divided: true },
].filter(Boolean)
const statusLabel = (value) => ({ active: '进行中', completed: '已完成', archived: '已归档' }[value] || value)
const formatTime = (value) => {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '刚刚' : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit' }).format(date)
}
watch(() => props.keyword, (value) => { keyword.value = value })
watch(() => props.status, (value) => { status.value = value })
function emitFilters() { emit('filter', { keyword: keyword.value.trim(), status: status.value, pageNum: 1, pageSize: props.pageSize }) }
</script>

<style scoped>
.recent-work { min-height: 0; display: flex; flex: 1; flex-direction: column; gap: 8px; padding: 12px 10px; color: var(--color-text); }
.recent-work__heading { min-height: 28px; display: flex; align-items: center; justify-content: space-between; color: var(--color-text-secondary); font-size: 12px; font-weight: 600; }
.recent-work__stale, .recent-work__error { margin: 0; padding: 8px; border-radius: var(--workspace-radius-sm); font-size: 12px; }
.recent-work__stale { color: var(--workspace-warning); background: var(--workspace-warning-soft); }
.recent-work__error { color: var(--workspace-danger); background: var(--workspace-danger-soft); }
.recent-work__loading { min-height: 0; flex: 1; overflow: hidden; }
.recent-work__loading :deep(.ant-spin-container) { height: 100%; }
.recent-work__list { height: 100%; overflow-y: auto; }
.recent-work__item { display: flex; align-items: center; gap: 4px; margin-bottom: 3px; padding: 7px 6px 7px 9px; border-radius: var(--workspace-radius-sm); }
.recent-work__item:hover { background: var(--gray-50); }
.recent-work__item.active { background: var(--main-50); color: var(--main-700); }
.recent-work__open { min-width: 0; display: grid; flex: 1; gap: 3px; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.recent-work__open:disabled { cursor: default; }
.recent-work__open strong, .recent-work__open small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.recent-work__open strong { font-size: 13px; font-weight: 500; }
.recent-work__open small { color: var(--color-text-secondary); font-size: 11px; }
.recent-work__item :deep(.workspace-more-menu) { opacity: 0; }
.recent-work__item:hover :deep(.workspace-more-menu), .recent-work__item:focus-within :deep(.workspace-more-menu) { opacity: 1; }
@media (hover: none) { .recent-work__item :deep(.workspace-more-menu) { opacity: 1; } }
</style>
