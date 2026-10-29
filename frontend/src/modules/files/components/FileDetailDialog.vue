<!-- Adapted from original FileDetailDialog, MIT licensed. -->
<template>
  <ResourceDrawer title="文件详细信息" :open="visible" :width="820" @close="close">
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <a-spin v-if="loading" />
    <a-descriptions v-if="detail" :column="2" bordered class="file-descriptions">
      <a-descriptions-item label="文件ID" :span="2">
        {{ detail.fileId }}
      </a-descriptions-item>
      <a-descriptions-item label="原始文件名">
        {{ detail.originalName }}
      </a-descriptions-item>
      <a-descriptions-item label="显示名称">
        {{ detail.displayName }}
      </a-descriptions-item>
      <a-descriptions-item label="访问类型">
        {{ accessTypeLabel(detail.accessType) }}
      </a-descriptions-item>
      <a-descriptions-item label="文件状态">
        {{ statusLabel(detail.status) }}
      </a-descriptions-item>
      <a-descriptions-item label="上传用户">
        {{ userLabel(detail.uploaderUserId) }}
      </a-descriptions-item>
      <a-descriptions-item label="上传用户ID">
        {{ detail.uploaderUserId || '-' }}
      </a-descriptions-item>
      <a-descriptions-item label="上传人权限">
        <a-tag
          v-if="detail.accessType === 'private' && detail.uploaderUserId"
          :color="detail.uploaderAccessEnabled ? 'blue' : 'default'"
        >
          {{ uploaderPermissionLabel(detail) }}
        </a-tag>
        <span v-else>{{ uploaderPermissionLabel(detail) }}</span>
      </a-descriptions-item>
      <a-descriptions-item label="所有者">
        {{ userLabel(detail.ownerUserId) }}
      </a-descriptions-item>
      <a-descriptions-item label="存储状态">
        {{ storageStatusLabel(detail.storageStatus) }}
      </a-descriptions-item>
      <a-descriptions-item label="ACL数量">
        {{ detail.aclEntryCount || 0 }}
      </a-descriptions-item>
      <a-descriptions-item label="业务引用">
        <a-button
          v-if="detail.referenceCount && canManageFile(identity, detail)"
          type="primary"
          ghost
          @click="emit('reference', detail)"
        >
          {{ detail.referenceCount }} 项
        </a-button>
        <span v-else>{{ detail.referenceCount }} 项</span>
      </a-descriptions-item>
      <a-descriptions-item label="权限版本">
        {{ detail.aclVersion ?? '-' }}
      </a-descriptions-item>
      <a-descriptions-item label="文件大小">
        {{ formatFileSize(detail.fileSize) }}
      </a-descriptions-item>
      <a-descriptions-item label="内容类型">
        {{ detail.mimeType || '-' }}
      </a-descriptions-item>
      <a-descriptions-item label="扩展名">
        {{ extension(detail.originalName) }}
      </a-descriptions-item>
      <a-descriptions-item label="上传时间">
        {{ formatTime(detail.createTime) }}
      </a-descriptions-item>
      <a-descriptions-item label="过期时间">
        {{ formatTime(detail.expireTime) || '-' }}
      </a-descriptions-item>
      <a-descriptions-item label="移入回收站" :span="2">
        {{ formatTime(detail.deletedAt) || '-' }}
      </a-descriptions-item>
      <a-descriptions-item label="派生来源" :span="2">
        {{ detail.sourceFileId || '原始文件' }}
      </a-descriptions-item>
      <a-descriptions-item label="SHA-256" :span="2">
        <span class="file-hash">{{ detail.sha256 }}</span>
      </a-descriptions-item>
    </a-descriptions>
    <template #footer>
      <div class="dialog-footer">
        <a-button
          v-if="
            detail?.status === 'active' && !detail.sourceFileId && canManageFile(identity, detail)
          "
          aria-label="编辑文件信息"
          @click="openEditor"
          >编辑信息</a-button
        >
        <a-button aria-label="关闭文件详情" @click="close">关 闭</a-button>
      </div>
    </template>
    <a-modal
      :open="editOpen"
      title="编辑文件信息"
      :confirm-loading="saving"
      ok-text="保存"
      :closable="!saving"
      :mask-closable="false"
      @ok="save"
      @cancel="editOpen = false"
    >
      <a-alert v-if="saveError" :message="saveError" type="error" show-icon />
      <a-form :model="form" layout="vertical">
        <a-form-item label="显示名称" name="name"
          ><a-input
            v-model:value="form.name"
            aria-label="显示名称"
            :maxlength="510"
            :disabled="saving"
        /></a-form-item>
        <a-form-item label="文件有效期" name="expires"
          ><a-date-picker
            v-bind="form.expires ? { value: form.expires } : {}"
            value-format="YYYY-MM-DD HH:mm:ss"
            show-time
            allow-clear
            :disabled="saving"
            placeholder="永久有效"
            @update:value="setExpiry"
        /></a-form-item>
        <a-form-item label="上传人权限"
          ><a-switch
            :checked="form.uploader"
            :disabled="saving"
            @update:checked="(value) => (form.uploader = value === true)"
        /></a-form-item>
      </a-form>
    </a-modal>
  </ResourceDrawer>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  DatePicker as ADatePicker,
  Descriptions as ADescriptions,
  DescriptionsItem as ADescriptionsItem,
  Form as AForm,
  FormItem as AFormItem,
  Input as AInput,
  Modal as AModal,
  Spin as ASpin,
  Switch as ASwitch,
  Tag as ATag
} from 'ant-design-vue'
import ResourceDrawer from '../../../shared/components/ResourceDrawer.vue'
import { identity } from '../../identity/public.ts'
import { canManageFile } from '../access.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import { getFile, updateFile } from '../api/index.ts'
import type { FileUser, ManagedFile } from '../api/index.ts'
import { useFilePanel } from '../composables/useFilePanel.ts'
import {
  accessTypeLabel,
  formatFileSize,
  formatTime,
  isoDateTime,
  localDateTime,
  statusLabel,
  storageStatusLabel
} from '../presentation.ts'
const props = defineProps<{ users: FileUser[] }>()
const emit = defineEmits<{ reference: [file: ManagedFile]; refresh: [] }>()
const panel = useFilePanel(getFile)
const { visible, loading, error } = panel
const detail = computed(() => panel.data.value)
const editOpen = ref(false),
  saving = ref(false),
  saveError = ref('')
const form = reactive({ name: '', expires: undefined as string | undefined, uploader: false })
const mutation = new RequestScope()
async function open(file: ManagedFile): Promise<void> {
  mutation.replace()
  editOpen.value = false
  saving.value = false
  saveError.value = ''
  await panel.open(file)
}
function close(): void {
  mutation.replace()
  editOpen.value = false
  saving.value = false
  panel.close()
}
function userLabel(id: string): string {
  return props.users.find((user) => user.id === id)?.username ?? id
}
function extension(name: string): string {
  return name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '-') : '-'
}
function uploaderPermissionLabel(file: ManagedFile): string {
  if (file.uploaderUserId === file.ownerUserId)
    return file.uploaderAccessEnabled ? '已保留；同时为所有者' : '已移除；仍通过所有者访问'
  return file.uploaderAccessEnabled ? '已保留，显式拒绝可覆盖' : '已移除，不再因上传身份放行'
}
function setExpiry(value: unknown): void {
  form.expires = typeof value === 'string' ? value : undefined
}
function openEditor(): void {
  const file = detail.value
  if (
    !file ||
    !canManageFile(identity.value, file) ||
    file.status !== 'active' ||
    file.sourceFileId
  )
    return
  Object.assign(form, {
    name: file.displayName,
    expires: localDateTime(file.expireTime),
    uploader: file.uploaderAccessEnabled
  })
  saveError.value = ''
  editOpen.value = true
}
async function save(): Promise<void> {
  const file = detail.value
  if (
    !file ||
    !canManageFile(identity.value, file) ||
    file.status !== 'active' ||
    file.sourceFileId ||
    saving.value
  )
    return
  const name = form.name.trim()
  if (!name || [...name].length > 255 || name.includes('\0') || /[\uD800-\uDFFF]/u.test(name)) {
    saveError.value = '请输入1到255字的有效名称'
    return
  }
  const context = mutation.replace()
  saving.value = true
  saveError.value = ''
  try {
    const result = await updateFile(
      file,
      {
        display_name: name,
        expires_at: isoDateTime(form.expires),
        uploader_access_enabled: form.uploader
      },
      context.signal
    )
    if (!context.isCurrent() || !visible.value || detail.value?.fileId !== file.fileId) return
    panel.data.value = result
    editOpen.value = false
    emit('refresh')
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) saveError.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) saving.value = false
  }
}
onBeforeUnmount(() => mutation.dispose())
defineExpose({ open })
</script>

<style scoped>
.file-descriptions :deep(.ant-descriptions-item-label) {
  white-space: nowrap;
}

.file-descriptions :deep(.ant-descriptions-item-content) {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.file-hash {
  word-break: break-all;
  font-family: var(--workspace-font-mono);
}
</style>
