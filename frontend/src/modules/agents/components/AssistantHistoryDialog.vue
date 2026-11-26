<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
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
import HistoryDialog from '../../../shared/components/HistoryDialog.vue'
import HistoryRecord from '../../../shared/components/HistoryRecord.vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { useConfirmation } from '../../../shared/composables/useConfirmation.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { canUseCapability } from '../../identity/public.ts'
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
const canRename = computed(() => canUseCapability('agents', 'conversation_update'))
const canDelete = computed(() => canUseCapability('agents', 'conversation_delete'))
const items = computed(() => [
  ...(canRename.value ? [{ command: 'rename', label: '重命名', icon: EditOutlined }] : []),
  ...(canDelete.value
    ? [
        {
          command: 'delete',
          label: '删除',
          icon: DeleteOutlined,
          danger: true,
          divided: canRename.value
        }
      ]
    : [])
])
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
    if (!canRename.value) return
    renameTarget.value = target
    title.value = target.title
    renameError.value = ''
    return
  }
  if (
    value !== 'delete' ||
    !canDelete.value ||
    !(await confirm(`是否删除“${target.title}”？删除后将无法在历史记录中找回。`))
  )
    return
  if (locked(target) || !canDelete.value) return
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
  if (!canRename.value || !target || mutation.value) return
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
  <HistoryDialog
    :open="open"
    title="历史记录"
    dialog-id="assistant-history-dialog"
    @update:open="emit('update:open', $event)"
  >
    <section class="history-list" aria-label="助手历史对话">
      <div class="history-list__heading">
        <span>最近会话</span>
        <RefreshButton label="刷新会话" :loading="loading" @click="emit('refresh')" />
      </div>
      <p v-if="error || failure" class="history-list__error" role="alert">{{ failure || error }}</p>
      <ASpin :spinning="loading" wrapper-class-name="history-list__loading">
        <HistoryRecord
          v-for="item in conversations"
          :key="item.id"
          class="assistant-session"
          :title="item.title"
          :timestamp="item.updatedAt"
          :selected="selectedId === item.id"
          @open="openConversation(item.id)"
        >
          <template #actions>
            <WorkspaceMoreMenu
              v-if="items.length && !locked(item)"
              compact
              :items="items"
              @command="(value) => command(value, item)"
            />
            <ASpin v-else-if="mutation === item.id" size="small" />
            <small v-else-if="locked(item)">处理中</small>
          </template>
        </HistoryRecord>
        <AEmpty
          v-if="!loading && !conversations.length"
          description="暂无会话"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        />
      </ASpin>
      <AButton
        v-if="more"
        class="history-list__pagination"
        type="text"
        :disabled="loading"
        @click="emit('more')"
        >加载更多会话</AButton
      >
    </section>
  </HistoryDialog>
  <AModal
    :open="canRename && renameTarget !== null"
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
