<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Empty as AEmpty,
  Input as AInput,
  Modal as AModal,
  Spin as ASpin
} from 'ant-design-vue'
import { DeleteOutlined, EditOutlined } from '@ant-design/icons-vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { deleteAssistantConversation, renameAssistantConversation } from '../api/conversations.ts'
import type { AssistantConversation } from '../api/conversations.ts'

const props = defineProps<{
  open: boolean
  agentId: string
  conversations: AssistantConversation[]
  selectedId?: string | undefined
  lockedId?: string | undefined
  pendingId?: string | undefined
  loading: boolean
  error: string
  more: boolean
}>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  select: [id: string]
  refresh: []
  more: []
  changed: [conversation: AssistantConversation]
  removed: [id: string]
}>()
const scope = new RequestScope()
const { confirm } = useConfirmation()
const mutation = ref(''),
  failure = ref('')
const renameTarget = ref<AssistantConversation | null>(null),
  title = ref(''),
  renameError = ref('')
const items = [
  { command: 'rename', label: '重命名', icon: EditOutlined },
  { command: 'delete', label: '删除', icon: DeleteOutlined, danger: true, divided: true }
]
function locked(item: AssistantConversation): boolean {
  return (
    item.id === props.lockedId ||
    item.id === props.pendingId ||
    !!item.preparationId ||
    !!mutation.value
  )
}
async function command(value: string, item: AssistantConversation): Promise<void> {
  if (locked(item)) return
  failure.value = ''
  const target = { ...item }
  if (value === 'rename') {
    renameTarget.value = target
    title.value = target.title
    renameError.value = ''
    return
  }
  if (
    value !== 'delete' ||
    !(await confirm(`是否删除“${target.title}”？删除后将无法在历史记录中找回。`))
  )
    return
  if (locked(target)) return
  const ticket = scope.replace()
  mutation.value = target.id
  try {
    await deleteAssistantConversation(props.agentId, target, ticket.signal)
    if (ticket.isCurrent()) emit('removed', target.id)
  } catch (error) {
    if (ticket.isCurrent() && !isAborted(error)) {
      failure.value = errorMessage(error)
      emit('refresh')
    }
  } finally {
    if (ticket.isCurrent()) mutation.value = ''
  }
}
async function rename(): Promise<void> {
  const target = renameTarget.value,
    value = title.value.trim()
  if (!target || mutation.value) return
  if (!value || [...value].length > 100) {
    renameError.value = '标题需为1–100字'
    return
  }
  const ticket = scope.replace()
  mutation.value = target.id
  renameError.value = ''
  try {
    const result = await renameAssistantConversation(props.agentId, target, value, ticket.signal)
    if (ticket.isCurrent()) {
      emit('changed', result)
      renameTarget.value = null
    }
  } catch (error) {
    if (ticket.isCurrent() && !isAborted(error)) {
      renameError.value = errorMessage(error)
      emit('refresh')
    }
  } finally {
    if (ticket.isCurrent()) mutation.value = ''
  }
}
function openConversation(id: string): void {
  emit('select', id)
  emit('update:open', false)
}
onBeforeUnmount(() => scope.dispose())
</script>

<template>
  <AModal
    :open="open"
    title="历史记录"
    centered
    :width="620"
    :footer="null"
    :style="{ maxWidth: 'calc(100vw - 32px)' }"
    @cancel="emit('update:open', false)"
  >
    <section id="assistant-history-dialog" class="assistant-history" aria-label="助手历史对话">
      <div class="assistant-history__heading">
        <span>最近会话</span
        ><RefreshButton label="刷新会话" :loading="loading" @click="emit('refresh')" />
      </div>
      <AAlert v-if="error || failure" type="error" :message="failure || error" show-icon />
      <ASpin :spinning="loading" wrapper-class-name="assistant-history__loading">
        <div
          v-for="item in conversations"
          :key="item.id"
          class="assistant-session"
          :class="{ active: selectedId === item.id }"
        >
          <button
            type="button"
            class="assistant-session__open"
            :aria-label="item.title"
            :aria-current="selectedId === item.id ? 'page' : undefined"
            @click="openConversation(item.id)"
          >
            <strong :title="item.title">{{ item.title }}</strong>
            <small>{{
              new Date(item.updatedAt).toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
              })
            }}</small>
          </button>
          <WorkspaceMoreMenu
            v-if="!locked(item)"
            compact
            :items="items"
            @command="(value) => command(value, item)"
          />
          <ASpin v-else-if="mutation === item.id" size="small" />
          <small v-else class="assistant-session__busy">处理中</small>
        </div>
        <AEmpty
          v-if="!loading && !conversations.length"
          description="暂无会话"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        />
      </ASpin>
      <AButton v-if="more" type="text" :disabled="loading" @click="emit('more')"
        >加载更多会话</AButton
      >
    </section>
  </AModal>
  <AModal
    :open="renameTarget !== null"
    title="重命名会话"
    :closable="!mutation"
    :mask-closable="false"
    @cancel="renameTarget = null"
  >
    <AAlert v-if="renameError" type="error" :message="renameError" show-icon />
    <AInput
      v-model:value="title"
      aria-label="会话标题"
      :maxlength="200"
      :disabled="!!mutation"
      @press-enter="rename"
    />
    <template #footer
      ><AButton aria-label="取消" :disabled="!!mutation" @click="renameTarget = null">取消</AButton
      ><AButton aria-label="保存" type="primary" :loading="!!mutation" @click="rename"
        >保存</AButton
      ></template
    >
  </AModal>
</template>

<style scoped>
.assistant-history {
  display: flex;
  min-height: min(180px, 65dvh);
  max-height: min(65dvh, 600px);
  flex-direction: column;
  gap: 9px;
  padding: 12px 10px;
  color: var(--color-text);
  overflow: hidden;
}
.assistant-history__heading {
  display: flex;
  flex-shrink: 0;
  min-height: 28px;
  align-items: center;
  justify-content: space-between;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}
.assistant-history__loading {
  min-height: 0;
  flex: 1;
  overflow: hidden auto;
  overscroll-behavior-y: contain;
}
.assistant-session {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 3px;
  padding: 7px 6px 7px 9px;
  border-radius: var(--workspace-radius-sm);
}
.assistant-session:hover {
  background: var(--gray-50);
}
.assistant-session.active {
  background: var(--main-50);
  color: var(--main-700);
}
.assistant-session__open {
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
.assistant-session__open strong,
.assistant-session__open small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.assistant-session__open strong {
  font-size: 13px;
  font-weight: 500;
}
.assistant-session__open small,
.assistant-session__busy {
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-caption);
  font-variant-numeric: tabular-nums;
}
.assistant-session :deep(.workspace-more-menu) {
  opacity: 0;
}
.assistant-session:hover :deep(.workspace-more-menu),
.assistant-session:focus-within :deep(.workspace-more-menu) {
  opacity: 1;
}
.assistant-session__open:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: 3px;
}
@media (hover: none) {
  .assistant-session :deep(.workspace-more-menu) {
    opacity: 1;
  }
}
</style>
