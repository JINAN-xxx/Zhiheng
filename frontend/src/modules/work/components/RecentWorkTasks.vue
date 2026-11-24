<!-- Adapted from original RecentWorkTasks, MIT licensed. -->
<template>
  <section class="recent-work history-list" aria-label="最近工作">
    <div class="history-list__heading">
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
      :value="keyword"
      allow-clear
      placeholder="搜索工作"
      :aria-label="status === 'archived' ? '搜索已归档工作' : '搜索最近工作'"
      @update:value="scheduleSearch"
      @press-enter="emitFilters"
    >
      <template #prefix><SearchOutlined /></template>
    </a-input>
    <p v-if="error && tasks.length" class="history-list__stale" role="status">
      刷新失败，列表可能已过期
    </p>
    <p v-else-if="error" class="history-list__error" role="alert">{{ error }}</p>
    <a-spin :spinning="loading" wrapper-class-name="history-list__loading">
      <div class="history-list__items">
        <HistoryRecord
          v-for="task in tasks"
          :key="task.taskId"
          class="recent-work__item"
          :title="task.title"
          :description="task.goal"
          :timestamp="task.updatedAt"
          :selected="task.taskId === activeTaskId"
          :disabled="!canOpen"
          :open-label="`${task.status === 'active' ? '打开工作' : '查看记录'}：${task.title}`"
          @open="canOpen && emit('open', task.taskId)"
        >
          <template #meta>
            <span class="recent-work__state" :class="{ 'is-running': task.currentRunId }">
              <LoadingOutlined v-if="task.currentRunId" />
              {{
                task.mode === 'legacy'
                  ? '历史任务'
                  : task.currentRunId
                    ? '正在执行'
                    : task.status === 'active'
                      ? '等待指令'
                      : statusLabel(task.status)
              }}
            </span>
          </template>
          <template #actions>
            <WorkspaceMoreMenu
              v-if="taskActions(task).length"
              compact
              :items="taskActions(task)"
              @command="handleCommand($event, task)"
            />
          </template>
        </HistoryRecord>
        <a-empty
          v-if="!loading && !tasks.length"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
          :description="
            keyword ? '没有匹配工作' : status === 'archived' ? '暂无已归档工作' : '暂无工作'
          "
        />
      </div>
    </a-spin>
    <a-pagination
      v-if="total > pageSize"
      class="history-list__pagination recent-work__pagination"
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
  InboxOutlined,
  LoadingOutlined,
  SearchOutlined
} from '@ant-design/icons-vue'
import {
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
import HistoryRecord from '../../../shared/components/HistoryRecord.vue'
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
  archive: [task: WorkSummary]
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
  if (props.canEdit && props.canManageTask(task, 'update')) {
    if (task.status === 'active')
      actions.push({ command: 'rename', label: '重命名', icon: EditOutlined })
    actions.push({
      command: 'archive',
      label: task.status === 'archived' ? '取消归档' : '归档',
      icon: InboxOutlined
    })
  }
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
  if (command === 'archive') emit('archive', task)
  if (command === 'delete') emit('delete', task)
}
const statusLabel = (value: WorkStatus) =>
  ({ active: '进行中', completed: '已完成', archived: '已归档' })[value]
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
  keyword.value = value
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
.recent-work__heading-actions {
  display: flex;
  align-items: center;
}
.recent-work__pagination {
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
.recent-work__filter.is-filtered {
  color: var(--main-700);
  background: var(--main-50);
  border-radius: var(--workspace-radius-sm);
}
.recent-work__state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.recent-work__state.is-running {
  color: var(--main-700);
}
</style>
