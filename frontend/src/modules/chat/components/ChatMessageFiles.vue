<!-- Adapted from original ChatMessageFiles, MIT licensed. -->
<script lang="ts">
interface AttachmentJoinDraft {
  requestId: string
  targetId: string
  targetName: string
}
const retainedJoinDrafts = new Map<string, AttachmentJoinDraft | null>()
</script>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Select as ASelect
} from 'ant-design-vue'
import { MoreOutlined } from '@ant-design/icons-vue'
import { RouterLink } from 'vue-router'
import { downloadFile, getFile } from '../../files/public.ts'
import { addChatAttachment, listBases } from '../../knowledge/public.ts'
import type { KnowledgeBase } from '../../knowledge/public.ts'
import { identity } from '../../identity/public.ts'
import { isDefiniteRejection, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { attachmentFile, attachmentPreview } from '../../../shared/messageFiles.ts'
import type { ChatAttachment } from '../api/index.ts'
import MessageFileGrid from '../../../shared/components/MessageFileGrid.vue'
import MessageFilePreview from '../../../shared/components/MessageFilePreview.vue'
import AttachmentParseWarnings from '../../../shared/components/AttachmentParseWarnings.vue'
const props = defineProps<{ files: ChatAttachment[] }>()
type OwnedAttachment = ChatAttachment & { owner_id?: string | null }
const selectedId = ref('')
const filesById = computed(() => new Map(props.files.map((file) => [file.file_id, file])))
const items = computed(() => props.files.map(attachmentFile))
const selected = computed(() => items.value.find((file) => file.id === selectedId.value) ?? null)
const accessKey = computed(() => identity.value?.id ?? null)
async function readPreview(id: string, signal: AbortSignal) {
  const file = items.value.find((item) => item.id === id)
  return file ? attachmentPreview(file, downloadFile, signal) : null
}
const metadataScope = new RequestScope(),
  joinScope = new RequestScope(),
  writeScope = new RequestScope()
const owners = ref<Record<string, string | null>>({})
const joinFile = ref<ChatAttachment | null>(null),
  targets = ref<KnowledgeBase[]>([]),
  targetId = ref<string | undefined>(),
  joinLoading = ref(false),
  joining = ref(false),
  joinError = ref(''),
  joinUncertain = ref(false),
  joinDone = ref(false),
  alreadyExists = ref(false)
const retainedTargetName = ref('')
let requestId = ''
const target = computed(() => targets.value.find((b) => b.id === targetId.value))
const targetOptions = computed(() => targets.value.map((b) => ({ value: b.id, label: b.name })))
const sharing = computed(() =>
  target.value?.access === 'public'
    ? '所有已登录用户'
    : target.value?.sharing_scope === 'users'
      ? '指定用户'
      : '仅自己'
)
function supported(file: ChatAttachment) {
  return (
    file.size > 0 &&
    file.size <= 20 * 1024 * 1024 &&
    ['pdf', 'docx', 'xlsx', 'txt', 'md', 'csv', 'json', 'html', 'htm'].includes(
      file.original_filename.split('.').at(-1)?.toLowerCase() ?? ''
    )
  )
}
function canJoin(file: OwnedAttachment) {
  const userId = identity.value?.id
  return !!userId && supported(file) && (file.owner_id ?? owners.value[file.file_id]) === userId
}
async function inspectFile(file: OwnedAttachment, opened: boolean) {
  if (!opened || !supported(file) || file.owner_id != null || file.file_id in owners.value) return
  const ticket = metadataScope.current()
  try {
    const metadata = await getFile(file.file_id, ticket.signal)
    if (ticket.isCurrent())
      owners.value = {
        ...owners.value,
        [file.file_id]:
          metadata.status === 'active' &&
          (!metadata.expireTime || Date.parse(metadata.expireTime) > Date.now())
            ? metadata.ownerUserId
            : null
      }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason))
      owners.value = { ...owners.value, [file.file_id]: null }
  }
}
const draftKey = (file: ChatAttachment) =>
  `zhiheng:knowledge-attachment:${identity.value?.id ?? ''}:${file.file_id}`
function clearJoinDraft(file: ChatAttachment): void {
  retainedJoinDrafts.set(draftKey(file), null)
  try {
    sessionStorage.removeItem(draftKey(file))
  } catch {
    /* Resolved memory state is retained. */
  }
}
async function openJoin(file: ChatAttachment) {
  if (!canJoin(file)) return
  joinFile.value = file
  joinLoading.value = true
  joinError.value = ''
  joinDone.value = false
  joinUncertain.value = false
  targetId.value = undefined
  retainedTargetName.value = ''
  targets.value = []
  requestId = createRequestId()
  const ticket = joinScope.replace()
  try {
    try {
      const saved = retainedJoinDrafts.get(draftKey(file))
      const raw = retainedJoinDrafts.has(draftKey(file))
        ? null
        : sessionStorage.getItem(draftKey(file))
      if (saved || raw) {
        const d =
          saved ??
          (JSON.parse(raw!) as { requestId?: unknown; targetId?: unknown; targetName?: unknown })
        if (
          typeof d.requestId === 'string' &&
          d.requestId &&
          typeof d.targetId === 'string' &&
          d.targetId
        ) {
          requestId = d.requestId
          targetId.value = d.targetId
          retainedTargetName.value = typeof d.targetName === 'string' ? d.targetName : d.targetId
          joinUncertain.value = true
        }
      }
    } catch {
      /* Optional recovery storage. */
    }
    const rows = await listBases(ticket.signal, 'owned')
    if (ticket.isCurrent())
      targets.value = rows
        .filter((b) => b.availability === 'available')
        .filter(
          (b) =>
            b.can_manage && b.owner_id === identity.value?.id && b.document_policy === 'library'
        )
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) joinError.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) joinLoading.value = false
  }
}
function closeJoin() {
  if (joining.value) return
  joinScope.replace()
  writeScope.replace()
  joinFile.value = null
}
async function submitJoin() {
  if (
    !joinFile.value ||
    !targetId.value ||
    (!target.value && !joinUncertain.value) ||
    joining.value ||
    joinLoading.value ||
    joinDone.value
  )
    return
  const file = joinFile.value,
    targetBaseId = targetId.value,
    wasUncertain = joinUncertain.value,
    ticket = writeScope.replace()
  joining.value = true
  joinError.value = ''
  retainedTargetName.value = target.value?.name ?? retainedTargetName.value
  try {
    const draft = { requestId, targetId: targetBaseId, targetName: retainedTargetName.value }
    retainedJoinDrafts.set(draftKey(file), draft)
    try {
      sessionStorage.setItem(draftKey(file), JSON.stringify(draft))
    } catch {
      /* Current modal retains the same request. */
    }
    const result = await addChatAttachment(
      targetBaseId,
      { file_id: file.file_id, client_request_id: requestId },
      ticket.signal
    )
    if (!ticket.isCurrent()) return
    joinDone.value = true
    joinUncertain.value = false
    alreadyExists.value = result.already_exists
    clearJoinDraft(file)
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) {
      joinError.value = errorMessage(reason)
      joinUncertain.value =
        wasUncertain ||
        !isDefiniteRejection(reason) ||
        (reason.status === 409 && reason.code === 'request_pending')
      if (!joinUncertain.value) {
        clearJoinDraft(file)
        requestId = createRequestId()
      }
    }
  } finally {
    if (ticket.isCurrent()) joining.value = false
  }
}
onBeforeUnmount(() => {
  metadataScope.dispose()
  joinScope.dispose()
  writeScope.dispose()
})
</script>
<template>
  <div v-if="files.length" class="message-files">
    <AttachmentParseWarnings :files="files" />
    <MessageFileGrid
      :files="items"
      :access-key="accessKey"
      :read-file="downloadFile"
      :read-preview="readPreview"
      label="消息附件"
      @preview="selectedId = $event"
    >
      <template #actions="{ file, download }">
        <a-dropdown
          v-if="filesById.get(file.id)"
          :trigger="['click']"
          @open-change="inspectFile(filesById.get(file.id)!, $event)"
        >
          <a-button type="text" size="small" :aria-label="'附件操作：' + file.name">
            <MoreOutlined />
          </a-button>
          <template #overlay>
            <a-menu>
              <a-menu-item key="view" @click="selectedId = file.id">查看</a-menu-item>
              <a-menu-item key="download" @click="download(file)">下载</a-menu-item>
              <a-menu-item
                v-if="canJoin(filesById.get(file.id)!)"
                key="join"
                @click="openJoin(filesById.get(file.id)!)"
                >加入知识库</a-menu-item
              >
            </a-menu>
          </template>
        </a-dropdown>
      </template>
    </MessageFileGrid>
    <MessageFilePreview
      v-if="selected"
      :file="selected"
      :files="items"
      :access-key="accessKey"
      :read-file="downloadFile"
      :read-preview="readPreview"
      @close="selectedId = ''"
      @select="selectedId = $event"
    />
    <a-modal
      :open="!!joinFile"
      title="加入知识库"
      width="560px"
      :mask-closable="false"
      :closable="!joining"
      :keyboard="!joining"
      :footer="joinDone ? null : undefined"
      :confirm-loading="joining"
      :ok-text="joinUncertain ? '确认原提交' : '加入并处理'"
      cancel-text="取消"
      :ok-button-props="{
        disabled: (!target && !joinUncertain) || !targetId || joinLoading || joinDone
      }"
      :cancel-button-props="{ disabled: joining }"
      @cancel="closeJoin"
      @ok="submitJoin"
    >
      <a-alert v-if="joinError" :message="joinError" type="error" show-icon class="join-alert" />
      <template v-if="joinDone"
        ><a-alert
          type="success"
          show-icon
          :message="
            alreadyExists
              ? '已在该知识库中，未重复创建资料'
              : '已加入「' + (target?.name || retainedTargetName) + '」'
          "
        />
        <p class="join-success">
          <RouterLink :to="'/knowledge/' + targetId + '/documents'">查看资料</RouterLink
          ><a-button type="link" @click="closeJoin">关闭</a-button>
        </p></template
      >
      <template v-else
        ><p class="join-filename">{{ joinFile?.original_filename }}</p>
        <label for="knowledge-target">目标知识库</label
        ><a-select
          id="knowledge-target"
          :value="targetId"
          aria-label="目标知识库"
          :options="targetOptions"
          :loading="joinLoading"
          :disabled="joining || joinUncertain"
          placeholder="选择自己创建的知识库"
          @update:value="targetId = typeof $event === 'string' ? $event : undefined" />
        <p v-if="!joinLoading && !targets.length" class="join-hint">
          暂无可管理的知识库，请先新建知识库或完成旧库迁移。
        </p>
        <template v-if="target"
          ><p class="join-hint">共享范围：{{ sharing }}</p>
          <a-alert
            v-if="sharing !== '仅自己'"
            type="info"
            show-icon
            message="加入后，该知识库的共享用户可查看这份资料。"
          />
          <p class="join-hint">
            将创建独立资料副本并自动处理。原会话和其他附件不随资料共享。
          </p></template
        ><a-alert
          v-if="joinUncertain && !joinLoading && !target"
          class="join-alert"
          type="warning"
          show-icon
          message="原目标知识库当前不可用。可保留请求稍后确认；确认仍使用原目标和请求标识。" /><a-alert
          v-if="joinUncertain"
          type="warning"
          show-icon
          message="提交结果尚未确认，重试会沿用原请求。关闭后将保留请求供稍后确认。"
      /></template>
    </a-modal>
  </div>
</template>
<style scoped>
.message-files {
  width: min(520px, 100%);
}
.join-alert {
  margin-bottom: 16px;
}
.join-filename {
  font-weight: 500;
  overflow-wrap: anywhere;
}
.join-hint {
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text-secondary);
}
label {
  display: block;
  font-size: 13px;
  margin-bottom: 8px;
}
:deep(.ant-select) {
  width: 100%;
}
.join-success {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0;
}
</style>
