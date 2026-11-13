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
import { computed, onBeforeUnmount, ref, shallowRef } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Select as ASelect
} from 'ant-design-vue'
import {
  DownloadOutlined,
  FileTextOutlined,
  MoreOutlined,
  PictureOutlined
} from '@ant-design/icons-vue'
import { RouterLink } from 'vue-router'
import { downloadFile, formatFileSize, getFile, saveBlob } from '../../files/public.ts'
import { addChatAttachment, listBases } from '../../knowledge/public.ts'
import type { KnowledgeBase } from '../../knowledge/public.ts'
import { identity } from '../../identity/public.ts'
import { ApiError, errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { createRequestId } from '../../../shared/requestId.ts'
import { imagePreviewBlob } from '../attachments.ts'
import type { ChatAttachment } from '../api/index.ts'
defineProps<{ files: ChatAttachment[] }>()
type OwnedAttachment = ChatAttachment & { owner_id?: string | null }
const pendingId = ref<string | null>(null),
  previewUrl = ref(''),
  previewText = ref(''),
  previewName = ref(''),
  previewKind = ref(''),
  previewOpen = ref(false),
  error = ref('')
const previewBlob = shallowRef<Blob | null>(null),
  scope = new RequestScope(),
  metadataScope = new RequestScope(),
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
function closePreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = ''
  previewBlob.value = null
  previewText.value = ''
  previewOpen.value = false
}
async function openFile(file: ChatAttachment, download = false) {
  const ticket = scope.replace()
  pendingId.value = file.file_id
  error.value = ''
  try {
    const blob = await downloadFile(file.file_id, ticket.signal)
    if (!ticket.isCurrent()) return
    if (download) {
      saveBlob(blob, file.original_filename)
      return
    }
    closePreview()
    previewName.value = file.original_filename
    previewBlob.value = blob
    previewOpen.value = true
    if (file.mime.startsWith('image/')) {
      const clean = await imagePreviewBlob(blob)
      if (!ticket.isCurrent()) return
      previewBlob.value = clean
      previewUrl.value = URL.createObjectURL(clean)
      previewKind.value = 'image'
    } else if (file.mime === 'application/pdf') {
      previewUrl.value = URL.createObjectURL(blob)
      previewKind.value = 'pdf'
    } else if (
      ['txt', 'md', 'csv', 'json', 'html', 'htm'].includes(
        file.original_filename.split('.').at(-1)?.toLowerCase() ?? ''
      )
    ) {
      previewKind.value = 'text'
      const text = await blob.text()
      if (ticket.isCurrent()) previewText.value = text
    } else {
      previewKind.value = 'unsupported'
    }
  } catch (reason) {
    if (ticket.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (ticket.isCurrent()) pendingId.value = null
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
        !(reason instanceof ApiError) ||
        reason.status >= 500 ||
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
  scope.dispose()
  metadataScope.dispose()
  joinScope.dispose()
  writeScope.dispose()
  closePreview()
})
</script>
<template>
  <div v-if="files.length" class="message-files">
    <div v-for="file in files" :key="file.file_id" class="message-file-group">
      <a-button
        class="message-file"
        size="small"
        :loading="pendingId === file.file_id"
        :title="file.original_filename + ' · ' + formatFileSize(file.size)"
        :aria-label="'查看附件：' + file.original_filename"
        @click="openFile(file)"
        ><PictureOutlined v-if="file.mime.startsWith('image/')" /><FileTextOutlined v-else /><span
          class="message-file-name"
          >{{ file.original_filename }}</span
        ><span class="message-file-size">{{ formatFileSize(file.size) }}</span></a-button
      >
      <a-dropdown :trigger="['click']" @open-change="inspectFile(file, $event)"
        ><a-button type="text" size="small" :aria-label="'附件操作：' + file.original_filename"
          ><MoreOutlined /></a-button
        ><template #overlay
          ><a-menu
            ><a-menu-item key="view" @click="openFile(file)">查看</a-menu-item
            ><a-menu-item key="download" @click="openFile(file, true)">下载</a-menu-item
            ><a-menu-item v-if="canJoin(file)" key="join" @click="openJoin(file)"
              >加入知识库</a-menu-item
            ></a-menu
          ></template
        ></a-dropdown
      >
    </div>
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <a-modal :open="previewOpen" :title="previewName" :width="800" @cancel="closePreview"
      ><img
        v-if="previewKind === 'image' && previewUrl"
        :src="previewUrl"
        :alt="previewName"
        class="file-preview"
      /><iframe
        v-else-if="previewKind === 'pdf' && previewUrl"
        :src="previewUrl"
        :title="previewName"
        class="pdf-preview"
        sandbox=""
      />
      <pre v-else-if="previewKind === 'text'" class="text-preview">{{ previewText }}</pre>
      <p v-else>当前格式请下载后查看。</p>
      <template #footer
        ><a-button @click="closePreview">关闭</a-button
        ><a-button type="primary" @click="previewBlob && saveBlob(previewBlob, previewName)"
          ><DownloadOutlined />下载</a-button
        ></template
      ></a-modal
    >
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
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0;
}
.message-file-group {
  display: flex;
  align-items: center;
  max-width: 100%;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
}
.message-file {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  height: auto;
  min-height: 30px;
  text-align: left;
  border: 0;
  background: transparent;
  box-shadow: none;
}
.message-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: min(320px, 43vw);
}
.message-file-size {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.file-preview {
  display: block;
  max-width: 100%;
  max-height: 65vh;
  margin: auto;
  object-fit: contain;
}
.pdf-preview {
  width: 100%;
  height: 65vh;
  border: 0;
}
.text-preview {
  max-height: 65vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
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
