<!-- Adapted from original RecentSessions.vue, MIT licensed. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Input as AInput,
  Spin as ASpin,
  Empty as AEmpty,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  Modal as AModal,
  Button as AButton
} from 'ant-design-vue'
import {
  DeleteOutlined,
  EditOutlined,
  InboxOutlined,
  ReloadOutlined,
  SearchOutlined
} from '@ant-design/icons-vue'
import { useChatNavigation } from '../composables/workspace.ts'
import type { Conversation } from '../api/index.ts'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
const emit = defineEmits<{ navigate: [] }>()
const {
  conversations,
  selected,
  loading,
  error,
  refresh,
  select,
  archivedFilter,
  setArchivedFilter,
  conversationLocked,
  changeConversation
} = useChatNavigation()
function openConversation(conversation: Conversation): void {
  void select(conversation)
  emit('navigate')
}
const query = ref(''),
  renameTitle = ref(''),
  renameTarget = ref<Conversation | null>(null),
  renaming = ref(false),
  renameError = ref('')
const { confirm } = useConfirmation()
function menuItems(conversation: Conversation): MenuAction[] {
  return [
    { command: 'rename', label: '重命名', icon: EditOutlined },
    { command: 'archive', label: conversation.archived ? '取消归档' : '归档', icon: InboxOutlined },
    { command: 'delete', label: '删除', icon: DeleteOutlined, danger: true, divided: true }
  ]
}
async function command(command: string, conversation: Conversation): Promise<void> {
  if (conversationLocked(conversation.id)) return
  const target = { ...conversation }
  if (command === 'rename') {
    renameTarget.value = target
    renameTitle.value = target.title
    renameError.value = ''
    return
  }
  if (command === 'delete') {
    if (!(await confirm(`是否删除“${target.title}”？删除后会解除附件的会话引用。`))) return
    await changeConversation(target, 'delete')
  } else if (command === 'archive') await changeConversation(target, { archived: !target.archived })
}
async function rename(): Promise<void> {
  const target = renameTarget.value,
    title = renameTitle.value.trim()
  if (!target || renaming.value) return
  if (!title || [...title].length > 100) {
    renameError.value = '标题需为1–100字'
    return
  }
  renaming.value = true
  try {
    if ((await changeConversation(target, { title })) && renameTarget.value?.id === target.id)
      renameTarget.value = null
  } finally {
    renaming.value = false
  }
}
const filtered = computed(() =>
  conversations.value.filter((item) =>
    item.title.toLocaleLowerCase().includes(query.value.toLocaleLowerCase())
  )
)
</script>
<template>
  <section class="recent-sessions" aria-label="对话列表">
    <div class="recent-sessions__heading">
      <ARadioGroup
        :value="archivedFilter ? 'archived' : 'active'"
        size="small"
        aria-label="会话状态"
        @update:value="(value) => setArchivedFilter(value === 'archived')"
        ><ARadioButton value="active">最近会话</ARadioButton
        ><ARadioButton value="archived">已归档</ARadioButton></ARadioGroup
      ><button
        type="button"
        class="recent-sessions__refresh"
        aria-label="刷新会话"
        @click="refresh"
      >
        <ReloadOutlined />
      </button>
    </div>
    <AInput v-model:value="query" allow-clear placeholder="搜索当前页会话" aria-label="搜索最近会话"
      ><template #prefix><SearchOutlined /></template
    ></AInput>
    <p v-if="error" class="recent-sessions__error" role="alert">{{ error }}</p>
    <ASpin :spinning="loading" wrapper-class-name="recent-sessions__loading">
      <div class="recent-sessions__list">
        <div
          v-for="conversation in filtered"
          :key="conversation.id"
          class="recent-session"
          :class="{ active: selected?.id === conversation.id }"
        >
          <button
            type="button"
            class="recent-session__open conversation-item"
            :aria-label="conversation.title"
            :aria-current="selected?.id === conversation.id ? 'page' : undefined"
            @click="openConversation(conversation)"
          >
            <strong>{{ conversation.title }}</strong
            ><small>{{
              new Date(conversation.created_at).toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
              })
            }}</small>
          </button>
          <WorkspaceMoreMenu
            v-if="!conversationLocked(conversation.id)"
            compact
            :items="menuItems(conversation)"
            @command="(value) => command(value, conversation)"
          />
        </div>
        <AEmpty
          v-if="!loading && !filtered.length"
          :description="query ? '当前页没有匹配会话' : '暂无会话'"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        /></div
    ></ASpin>
  </section>
  <AModal
    :open="renameTarget !== null"
    title="重命名会话"
    :closable="!renaming"
    :mask-closable="false"
    @cancel="renameTarget = null"
  >
    <p v-if="renameError" role="alert">{{ renameError }}</p>
    <AInput
      v-model:value="renameTitle"
      aria-label="会话标题"
      :maxlength="200"
      :disabled="renaming"
      @press-enter="rename"
    />
    <template #footer
      ><AButton :disabled="renaming" @click="renameTarget = null">取消</AButton
      ><AButton type="primary" :loading="renaming" @click="rename">保存</AButton></template
    >
  </AModal>
</template>
<style scoped lang="scss">
.recent-sessions {
  min-height: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 9px;
  padding: 12px 10px;
  color: var(--color-text);
}
.recent-sessions__heading {
  display: flex;
  min-height: 28px;
  align-items: center;
  justify-content: space-between;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}
.recent-sessions__heading :deep(.ant-radio-button-wrapper) {
  padding-inline: 9px;
  font-size: 12px;
}
.recent-sessions__pagination {
  align-self: center;
}
.recent-sessions__refresh {
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
.recent-sessions__stale,
.recent-sessions__error {
  margin: 0;
  padding: 8px;
  border-radius: var(--workspace-radius-sm);
  font-size: 12px;
}
.recent-sessions__stale {
  color: var(--workspace-warning);
  background: var(--workspace-warning-soft);
}
.recent-sessions__error {
  color: var(--workspace-danger);
  background: var(--workspace-danger-soft);
}
.recent-sessions__list {
  min-height: 0;
  height: 100%;
  flex: 1;
  overflow-y: auto;
}
.recent-sessions__loading {
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
.recent-sessions__loading :deep(.ant-spin-container) {
  height: 100%;
}
.recent-session {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 3px;
  padding: 7px 6px 7px 9px;
  border-radius: var(--workspace-radius-sm);
}
.recent-session:hover {
  background: var(--gray-50);
}
.recent-session.active {
  background: var(--main-50);
  color: var(--main-700);
}
.recent-session__open {
  min-width: 0;
  display: grid;
  flex: 1;
  gap: 3px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.recent-session__open strong,
.recent-session__open small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.recent-session__open strong {
  font-size: 13px;
  font-weight: 500;
}
.recent-session__open small {
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-caption);
  line-height: var(--workspace-line-height);
  font-variant-numeric: tabular-nums;
}
.recent-session.active .recent-session__open small {
  color: var(--color-text-secondary);
}
.recent-sessions :deep(.ant-input-affix-wrapper) {
  border-color: var(--gray-150);
  background: var(--bg-container);
  box-shadow: none;
}
.recent-sessions :deep(.ant-input-affix-wrapper input),
.recent-sessions :deep(.ant-input-prefix),
.recent-sessions :deep(.ant-input-clear-icon) {
  color: var(--color-text);
  background: transparent;
}
.recent-sessions :deep(.ant-empty-description) {
  color: var(--color-text-secondary);
}
.recent-session :deep(.workspace-more-menu) {
  opacity: 0;
}
.recent-session:hover :deep(.workspace-more-menu),
.recent-session:focus-within :deep(.workspace-more-menu) {
  opacity: 1;
}
@media (hover: none) {
  .recent-session :deep(.workspace-more-menu) {
    opacity: 1;
  }
}
</style>
