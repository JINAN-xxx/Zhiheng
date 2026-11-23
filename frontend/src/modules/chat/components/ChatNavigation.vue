<!-- Adapted from original RecentSessions.vue, MIT licensed. -->
<script setup lang="ts">
import { ref } from 'vue'
import { canUseCapability } from '../../identity/public.ts'
import {
  Input as AInput,
  Spin as ASpin,
  Empty as AEmpty,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton,
  Modal as AModal,
  Button as AButton
} from 'ant-design-vue'
import { DeleteOutlined, EditOutlined, InboxOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { useChatNavigation } from '../composables/workspace.ts'
import type { Conversation } from '../api/index.ts'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import HistoryRecord from '../../../shared/components/HistoryRecord.vue'
import type { MenuAction } from '../../../shared/components/WorkspaceMoreMenu.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
const emit = defineEmits<{ navigate: [] }>()
const {
  conversations,
  search: query,
  setSearch,
  more,
  moreLoading,
  loadMore,
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
const renameTitle = ref(''),
  renameTarget = ref<Conversation | null>(null),
  renaming = ref(false),
  renameError = ref('')
const { confirm } = useConfirmation()
function menuItems(conversation: Conversation): MenuAction[] {
  return [
    ...(canUseCapability('chat', 'update')
      ? [
          { command: 'rename', label: '重命名', icon: EditOutlined },
          {
            command: 'archive',
            label: conversation.archived ? '取消归档' : '归档',
            icon: InboxOutlined
          }
        ]
      : []),
    ...(canUseCapability('chat', 'delete')
      ? [{ command: 'delete', label: '删除', icon: DeleteOutlined, danger: true, divided: true }]
      : [])
  ]
}
async function command(command: string, conversation: Conversation): Promise<void> {
  if (!canUseCapability('chat', command === 'delete' ? 'delete' : 'update')) return
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
  if (!target || renaming.value || !canUseCapability('chat', 'update')) return
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
</script>
<template>
  <section class="recent-sessions history-list" aria-label="对话列表">
    <div class="history-list__heading">
      <ARadioGroup
        :value="archivedFilter ? 'archived' : 'active'"
        size="small"
        aria-label="会话状态"
        @update:value="(value) => setArchivedFilter(value === 'archived')"
        ><ARadioButton value="active">最近会话</ARadioButton
        ><ARadioButton value="archived">已归档</ARadioButton></ARadioGroup
      ><RefreshButton label="刷新会话" :loading="loading" @click="refresh" />
    </div>
    <AInput
      :value="query"
      allow-clear
      :maxlength="200"
      placeholder="搜索会话"
      aria-label="搜索最近会话"
      @update:value="setSearch"
      ><template #prefix><SearchOutlined /></template
    ></AInput>
    <p v-if="error" class="history-list__error" role="alert">{{ error }}</p>
    <ASpin :spinning="loading" wrapper-class-name="history-list__loading">
      <div class="recent-sessions__list">
        <HistoryRecord
          v-for="conversation in conversations"
          :key="conversation.id"
          class="recent-session"
          :title="conversation.title"
          :timestamp="conversation.last_activity_at ?? conversation.created_at"
          :selected="selected?.id === conversation.id"
          @open="openConversation(conversation)"
        >
          <template #actions>
            <WorkspaceMoreMenu
              v-if="!conversationLocked(conversation.id) && menuItems(conversation).length"
              compact
              :items="menuItems(conversation)"
              @command="(value) => command(value, conversation)"
            />
          </template>
        </HistoryRecord>
        <AEmpty
          v-if="!loading && !conversations.length"
          :description="query ? '没有匹配会话' : '暂无会话'"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        /></div
    ></ASpin>
    <AButton
      v-if="more"
      class="history-list__pagination"
      type="text"
      :loading="moreLoading"
      :disabled="loading"
      @click="loadMore"
      >加载更多会话</AButton
    >
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
