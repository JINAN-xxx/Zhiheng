<script setup lang="ts">
import { Alert as AAlert, Button as AButton, Modal as AModal } from 'ant-design-vue'
defineProps<{
  open: boolean
  title: string
  saving: boolean
  loading?: boolean
  error: string
  width?: number
  saveLabel?: string
}>()
const emit = defineEmits<{ close: []; save: [] }>()
</script>
<template>
  <AModal
    :open="open"
    :title="title"
    :width="width || 560"
    :mask-closable="false"
    :keyboard="!saving"
    :closable="!saving"
    wrap-class-name="admin-dialog"
    centered
    destroy-on-close
    @cancel="!saving && emit('close')"
  >
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <form class="admin-form" :inert="saving" @submit.prevent="emit('save')">
      <slot /><button class="admin-submit" type="submit" tabindex="-1" aria-hidden="true" />
    </form>
    <template #footer
      ><AButton :disabled="saving" @click="emit('close')">取消</AButton
      ><AButton
        type="primary"
        :loading="saving"
        :disabled="Boolean(loading)"
        @click="emit('save')"
        >{{ saveLabel || '保存' }}</AButton
      ></template
    >
  </AModal>
</template>
<style>
.admin-dialog .ant-modal-content {
  border-radius: 12px;
}
.admin-dialog .ant-modal-title {
  font-size: 17px;
  font-weight: 500;
}
.admin-dialog .ant-modal-body {
  max-height: calc(100dvh - 190px);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.admin-dialog .ant-modal-footer {
  margin-top: 24px;
}
.admin-form {
  display: grid;
  gap: 18px;
  padding-top: 18px;
}
.admin-form label:not(.ant-checkbox-wrapper) {
  display: grid;
  align-content: start;
  gap: 8px;
  min-width: 0;
  color: var(--color-text);
  font-size: 13px;
}
.admin-form .ant-input-number,
.admin-form .ant-select {
  width: 100%;
  min-width: 0;
}
.admin-form .admin-check {
  display: flex;
  align-items: center;
  gap: 8px;
}
.admin-form__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: 16px;
  min-width: 0;
}
.admin-form small {
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.admin-submit {
  display: none;
}
@media (max-width: 600px) {
  .admin-form__grid {
    grid-template-columns: 1fr;
  }
  .admin-dialog {
    max-width: 100vw;
  }
}
</style>
