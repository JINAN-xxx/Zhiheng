<template>
  <ResourceDrawer
    title="文件详细信息"
    :open="open"
    :width="820"
    @close="open = false"
  >
    <a-descriptions
      :column="2"
      bordered
      class="file-descriptions"
    >
      <a-descriptions-item label="文件ID" :span="2">
        {{ detail.fileId }}
      </a-descriptions-item>
      <a-descriptions-item label="原始文件名">
        {{ detail.originalName }}
      </a-descriptions-item>
      <a-descriptions-item label="存储文件名">
        {{ detail.storedName }}
      </a-descriptions-item>
      <a-descriptions-item label="访问类型">
        {{ accessTypeLabel(detail.accessType) }}
      </a-descriptions-item>
      <a-descriptions-item label="文件状态">
        {{ statusLabel(detail.status) }}
      </a-descriptions-item>
      <a-descriptions-item label="上传用户">
        {{ detail.createBy || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="上传用户ID">
        {{ detail.uploadUserId || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="上传人权限">
        <a-tag
          v-if="detail.accessType === 'private' && detail.uploadUserId"
          :color="tagColor(uploaderPermissionTagType(detail))"
        >
          {{ uploaderPermissionLabel(detail) }}
        </a-tag>
        <span v-else>{{ uploaderPermissionLabel(detail) }}</span>
      </a-descriptions-item>
      <a-descriptions-item label="所有者">
        {{ detail.ownerName || detail.ownerUserId || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="所属部门">
        {{ detail.deptName || detail.deptId || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="存储状态">
        {{ storageStatusLabel(detail.storageStatus) }}
      </a-descriptions-item>
      <a-descriptions-item label="ACL数量">
        {{ detail.aclEntryCount || 0 }}
      </a-descriptions-item>
      <a-descriptions-item label="业务引用">
        <a-button
          v-if="detail.referenceCount"
          type="primary"
          ghost
          @click="emit('reference', detail)"
        >
          {{ detail.referenceCount }} 项
        </a-button>
        <span v-else>0 项</span>
      </a-descriptions-item>
      <a-descriptions-item label="权限版本">
        {{ detail.aclVersion ?? "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="文件大小">
        {{ formatFileSize(detail.fileSize) }}
      </a-descriptions-item>
      <a-descriptions-item label="内容类型">
        {{ detail.contentType || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="扩展名">
        {{ detail.extension || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="上传时间">
        {{ parseTime(detail.createTime) }}
      </a-descriptions-item>
      <a-descriptions-item label="过期时间">
        {{ parseTime(detail.expireTime) || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="移入回收站" :span="2">
        {{ parseTime(detail.deletedTime) || "-" }}
      </a-descriptions-item>
      <a-descriptions-item label="存储相对路径" :span="2">
        {{ detail.storageKey }}
      </a-descriptions-item>
      <a-descriptions-item label="SHA-256" :span="2">
        <span class="file-hash">{{ detail.fileHash }}</span>
      </a-descriptions-item>
    </a-descriptions>
    <template #footer>
      <div class="dialog-footer">
        <a-button @click="open = false">关 闭</a-button>
      </div>
    </template>
  </ResourceDrawer>
</template>

<script setup>
import ResourceDrawer from '@/components/Antd/ResourceDrawer.vue'
import {
  accessTypeLabel,
  formatFileSize,
  statusLabel,
  storageStatusLabel
} from "./fileFormatters";

defineProps({
  detail: {
    type: Object,
    default: () => ({})
  }
});

const emit = defineEmits(["reference"]);
const open = defineModel({
  type: Boolean,
  default: false
});
const tagColor = (type) => ({ primary: 'blue', warning: 'orange', info: 'default' }[type] || type)

function uploaderPermissionLabel(file) {
  if (file.accessType !== "private") {
    return "公开文件无需单独授权";
  }
  if (!file.uploadUserId) {
    return "未记录上传人";
  }
  const accessEnabled =
    file.uploaderAccessEnabled === "1" ||
    file.uploaderAccessEnabled === true;
  if (file.uploadUserId === file.ownerUserId) {
    return accessEnabled
      ? "兼容访问已保留；同时为所有者"
      : "兼容访问已移除；仍通过所有者访问";
  }
  return accessEnabled
    ? "已保留，显式拒绝可覆盖"
    : "已移除，不再因上传身份放行";
}

function uploaderPermissionTagType(file) {
  if (
    file.uploaderAccessEnabled !== "1" &&
    file.uploaderAccessEnabled !== true
  ) {
    return "info";
  }
  return file.uploadUserId === file.ownerUserId ? "primary" : "warning";
}
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
  font-family: monospace;
}
</style>
