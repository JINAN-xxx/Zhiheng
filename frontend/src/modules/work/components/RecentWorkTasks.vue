<!-- Adapted from original RecentWorkTasks, MIT licensed. -->
<template>
  <section class="recent-work" aria-label="最近工作">
    <div class="recent-work__heading">
      <a-radio-group
        :value="status === 'archived' ? 'archived' : ''"
        size="small"
        aria-label="工作归档状态"
        @update:value="changeArchiveTab"
      >
        <a-radio-button value="">最近工作</a-radio-button>
        <a-radio-button value="archived">已归档</a-radio-button>
      </a-radio-group>
      <div class="recent-work__heading-actions">
        <a-dropdown
          v-if="status !== 'archived'"
          :trigger="['click']"
          placement="bottomRight"
          overlay-class-name="workspace-more-popup"
        >
          <button
            type="button"
            class="recent-work__filter"
            :class="{ 'is-filtered': status }"
            aria-label="筛选工作状态"
            :title="status ? `当前筛选：${statusLabel(status)}` : '筛选工作状态'"
          >
            <FilterOutlined />
          </button>
          <template #overlay>
            <a-menu
              :selected-keys="[status || 'all']"
              @click="({ key }) => changeStatusFilter(String(key))"
            >
              <a-menu-item key="all">全部工作</a-menu-item>
              <a-menu-item key="active">进行中</a-menu-item>
              <a-menu-item key="completed">已完成</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        <RefreshButton label="刷新工作列表" :loading="loading" @click="emitFilters" />
      </div>
    </div>
    <a-input
      v-model:value="keyword"
      allow-clear
      placeholder="搜索工作"
      :aria-label="status === 'archived' ? '搜索已归档工作' : '搜索最近工作'"
      @update:value="scheduleSearch"
      @press-enter="emitFilters"
    >
      <template #prefix><SearchOutlined /></template>
    </a-input>
    <p v-if="error && tasks.length" class="recent-work__stale" role="status">
      刷新失败，列表可能已过期
    </p>
    <p v-else-if="error" class="recent-work__error" role="alert">{{ error }}</p>
    <a-spin :spinning="loading" wrapper-class-name="recent-work__loading">
      <div class="recent-work__list workspace-scroll-area">
        <ResourceCard
          v-for="task in tasks"
          :key="task.taskId"
          class="recent-work__item"
          :class="{ active: task.taskId === activeTaskId }"
          :title="task.title"
          :status="statusLabel(task.status)"
          :tone="
            task.currentRunId ? 'processing' : task.status === 'completed' ? 'positive' : 'neutral'
          "
          icon-tone="blue"
          compact
          :selected="task.taskId === activeTaskId"
          :interactive="canOpen"
          :open-label="`${task.status === 'active' ? '打开工作' : '查看记录'}：${task.title}`"
          @open="canOpen && emit('open', task.taskId)"
        >
          <template #icon><ProjectOutlined /></template>
          <p class="recent-work__goal" :title="task.goal">{{ task.goal }}</p>
          <span class="recent-work__run" :class="{ 'is-running': task.currentRunId }">
            <LoadingOutlined v-if="task.currentRunId" />
            {{
              task.currentRunId
                ? '正在执行'
                : task.status === 'active'
                  ? '等待指令'
                  : task.status === 'completed'
                    ? '工作已完成'
                    : '历史记录'
            }}
          </span>
          <template #footer-info>
            <time :datetime="task.updatedAt" :title="task.updatedAt">{{
              formatTime(task.updatedAt)
            }}</time>
          </template>
          <template #actions>
            <a-button
              size="small"
              type="text"
              class="recent-work__open resource-card-primary"
              :aria-current="task.taskId === activeTaskId ? 'page' : undefined"
              :disabled="!canOpen"
              @click="canOpen && emit('open', task.taskId)"
              >{{ task.status === 'active' ? '打开工作' : '查看记录' }}</a-button
            >
            <WorkspaceMoreMenu
              v-if="taskActions(task).length"
              compact
              :items="taskActions(task)"
              @command="handleCommand($event, task)"
            />
          </template>
        </ResourceCard>
        <a-empty
          v-if="!loading && !tasks.length"
          :description="
            keyword ? '没有匹配工作' : status === 'archived' ? '暂无已归档工作' : '暂无工作'
          "
        />
      </div>
    </a-spin>
    <a-pagination
      v-if="total > pageSize"
      class="recent-work__pagination"
      simple
      size="small"
      :current="pageNum"
      :page-size="pageSize"
      :total="total"
      :show-size-changer="false"
      @change="changePage"
    />
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  FilterOutlined,
  LoadingOutlined,
  ProjectOutlined,
  SearchOutlined
} from '@ant-design/icons-vue'
import {
  Button as AButton,
  Dropdown as ADropdown,
  Empty as AEmpty,
  Input as AInput,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Pagination as APagination,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  Spin as ASpin
} from 'ant-design-vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import ResourceCard from '../../../shared/components/ResourceCard.vue'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
import type { WorkSummary, WorkFilters, WorkStatus } from '../api/index.ts'
const props = defineProps<{
  tasks: WorkSummary[]
  activeTaskId: string
  loading: boolean
  error: string
  total: number
  pageNum: number
  pageSize: number
  canOpen: boolean
  canEdit: boolean
  canRemove: boolean
  canManageTask: (task: WorkSummary, action?: 'update' | 'delete') => boolean
  keyword: string
  status: WorkStatus | ''
}>()
const emit = defineEmits<{
  delete: [task: WorkSummary]
  filter: [query: WorkFilters]
  open: [id: string]
  rename: [task: WorkSummary]
}>()
const keyword = ref(props.keyword),
  status = ref(props.status)
let searchTimer: ReturnType<typeof setTimeout> | undefined
const taskActions = (task: WorkSummary): MenuAction[] => {
  const actions: MenuAction[] = []
  if (!props.canManageTask(task, 'update') && !props.canManageTask(task, 'delete')) return actions
  if (
    props.canEdit &&
    props.canManageTask(task, 'update') &&
    task.status === 'active' &&
    !task.currentRunId
  )
    actions.push({ command: 'rename', label: '重命名', icon: EditOutlined })
  if (props.canRemove && props.canManageTask(task, 'delete') && !task.currentRunId)
    actions.push({
      command: 'delete',
      label: '删除工作',
      icon: DeleteOutlined,
      danger: true,
      divided: true
    })
  return actions
}
function handleCommand(command: string, task: WorkSummary): void {
  if (command === 'rename') emit('rename', task)
  if (command === 'delete') emit('delete', task)
}
const statusLabel = (value: WorkStatus) =>
  ({ active: '进行中', completed: '已完成', archived: '已归档' })[value]
const formatTime = (value: string) =>
  Number.isNaN(new Date(value).getTime())
    ? '刚刚'
    : new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      }).format(new Date(value))
watch(
  () => props.keyword,
  (value) => {
    keyword.value = value
  }
)
watch(
  () => props.status,
  (value) => {
    status.value = value
  }
)
function emitFilters(): void {
  clearTimeout(searchTimer)
  emit('filter', {
    keyword: keyword.value.trim(),
    status: status.value,
    pageNum: 1,
    pageSize: props.pageSize
  })
}
function scheduleSearch(value: string): void {
  clearTimeout(searchTimer)
  if (!value.trim()) emitFilters()
  else searchTimer = setTimeout(emitFilters, 250)
}
function changeArchiveTab(value: unknown): void {
  status.value = value === 'archived' ? 'archived' : ''
  keyword.value = ''
  emitFilters()
}
function changeStatusFilter(value: string): void {
  status.value = value === 'active' || value === 'completed' ? value : ''
  emitFilters()
}
function changePage(page: number): void {
  clearTimeout(searchTimer)
  if (keyword.value.trim() !== props.keyword.trim()) return emitFilters()
  emit('filter', {
    keyword: keyword.value.trim(),
    status: status.value,
    pageNum: page,
    pageSize: props.pageSize
  })
}
onBeforeUnmount(() => clearTimeout(searchTimer))
</script>

<style scoped lang="scss">
.recent-work {
  min-height: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 9px;
  padding: 13px 10px;
  color: var(--color-text);
}
.recent-work__heading {
  display: flex;
  flex-shrink: 0;
  min-height: 28px;
  align-items: center;
  justify-content: space-between;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}
.recent-work__heading :deep(.ant-radio-button-wrapper) {
  padding-inline: 9px;
  font-size: 12px;
}
.recent-work__pagination {
  flex-shrink: 0;
  align-self: center;
}
.recent-work__filter {
  display: inline-flex;
  width: 30px;
  height: 30px;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.recent-work__stale,
.recent-work__error {
  margin: 0;
  padding: 8px;
  border-radius: var(--workspace-radius-sm);
  font-size: 12px;
}
.recent-work__stale {
  color: var(--workspace-warning);
  background: var(--workspace-warning-soft);
}
.recent-work__error {
  color: var(--workspace-danger);
  background: var(--workspace-danger-soft);
}
.recent-work__list {
  min-height: 0;
  height: 100%;
  flex: 1;
  overflow: hidden auto;
}
.recent-work__loading {
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
.recent-work__loading :deep(.ant-spin-container) {
  height: 100%;
}
.recent-work__item {
  margin-bottom: 10px;
}
.recent-work__goal {
  display: -webkit-box;
  min-height: 36px;
  margin: 0 0 6px;
  overflow: hidden;
  overflow-wrap: anywhere;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.7;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.recent-work__run {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.recent-work__run.is-running {
  color: var(--main-700);
}
.recent-work__open {
  font-size: 12px;
}
.recent-work :deep(.ant-input-affix-wrapper) {
  flex-shrink: 0;
  flex-shrink: 0;
  border-color: var(--gray-150);
  background: var(--bg-container);
  box-shadow: none;
}
.recent-work :deep(.ant-input-affix-wrapper input),
.recent-work :deep(.ant-input-prefix),
.recent-work :deep(.ant-input-clear-icon) {
  color: var(--color-text);
  background: transparent;
}
.recent-work :deep(.ant-empty-description) {
  color: var(--color-text-secondary);
}
.recent-work__heading-actions {
  display: flex;
  align-items: center;
}
.recent-work__filter.is-filtered {
  color: var(--main-700);
  background: var(--main-50);
  border-radius: var(--workspace-radius-sm);
}
.recent-work__open:disabled {
  cursor: default;
}
</style>
