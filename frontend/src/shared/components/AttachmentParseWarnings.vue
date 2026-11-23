<script setup lang="ts">
import { computed } from 'vue'
import { Alert as AAlert } from 'ant-design-vue'

const props = defineProps<{
  files: readonly { file_id: string; original_filename: string; parse_warning?: string }[]
}>()
const warnings = computed(() => props.files.filter((file) => file.parse_warning?.trim()))
</script>

<template>
  <div v-if="warnings.length" class="attachment-parse-warnings" aria-label="附件解析提示">
    <AAlert
      v-for="file in warnings"
      :key="file.file_id"
      type="warning"
      show-icon
      :message="`${file.original_filename}：${file.parse_warning}`"
    />
  </div>
</template>

<style scoped>
.attachment-parse-warnings {
  flex: 0 0 100%;
  width: 100%;
  min-width: 0;
  margin-top: 4px;
}
.attachment-parse-warnings :deep(.ant-alert + .ant-alert) {
  margin-top: 6px;
}
.attachment-parse-warnings :deep(.ant-alert-message) {
  overflow-wrap: anywhere;
  font-size: 13px;
  line-height: 1.6;
}
</style>
