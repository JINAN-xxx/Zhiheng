<script setup lang="ts">
import { Alert as AAlert, Button as AButton, Modal as AModal } from 'ant-design-vue'
defineProps<{
  open: boolean
  title: string
  saving: boolean
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
    destroy-on-close
    @cancel="!saving && emit('close')"
  >
    <AAlert v-if="error" :message="error" type="error" show-icon />
    <form class="admin-form" @submit.prevent="emit('save')">
      <slot /><button class="admin-submit" type="submit" tabindex="-1" aria-hidden="true" />
    </form>
    <template #footer
      ><AButton :disabled="saving" @click="emit('close')">取消</AButton
      ><AButton type="primary" :loading="saving" @click="emit('save')">{{
        saveLabel || '保存'
      }}</AButton></template
    >
  </AModal>
</template>
<style>
.admin-form {
  display: grid;
  gap: 16px;
  padding-top: 16px;
}
.admin-form label {
  display: grid;
  gap: 7px;
  color: var(--color-text);
  font-size: 13px;
}
.admin-form input:not([type='checkbox']),
.admin-form select,
.admin-form textarea {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  min-height: 34px;
  padding: 6px 10px;
  border: 1px solid var(--gray-200, #d9d9d9);
  border-radius: 6px;
  background: var(--bg-container);
  color: var(--color-text);
  font: inherit;
}
.admin-form select[multiple] {
  min-height: 96px;
}
.admin-form input:focus,
.admin-form select:focus,
.admin-form textarea:focus {
  outline: 2px solid var(--main-200);
  outline-offset: 1px;
}
.admin-form input:disabled {
  color: var(--color-text-secondary);
  background: var(--gray-50);
}
.admin-form .admin-check {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8px;
}
.admin-form .admin-check input {
  accent-color: var(--main-700);
}
.admin-form__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.admin-form small {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.admin-submit {
  display: none;
}
@media (max-width: 600px) {
  .admin-form__grid {
    grid-template-columns: 1fr;
  }
}
</style>
