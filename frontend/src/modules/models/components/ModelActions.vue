<script setup lang="ts">
import {
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import { EllipsisOutlined } from '@ant-design/icons-vue'
import type { ChatModel } from '../api/index.ts'

defineProps<{ model: ChatModel; busy: boolean; testing: boolean }>()
defineEmits<{
  edit: []
  rates: []
  test: []
  control: [action: 'enable' | 'disable' | 'publish' | 'unpublish' | 'delete']
}>()
</script>

<template>
  <div class="model-actions">
    <div class="desktop-actions">
      <AButton type="link" size="small" aria-label="编辑模型" @click="$emit('edit')">编辑</AButton>
      <AButton type="link" size="small" aria-label="费率配置" @click="$emit('rates')">费率</AButton>
      <AButton
        type="link"
        size="small"
        aria-label="测试连接"
        :loading="testing && busy"
        :disabled="busy || !model.enabled"
        @click="$emit('test')"
        >测试</AButton
      >
    </div>
    <ADropdown :trigger="['click']">
      <AButton type="text" aria-label="更多操作"><EllipsisOutlined /></AButton>
      <template #overlay>
        <AMenu>
          <AMenuItem @click="$emit('edit')">编辑模型</AMenuItem>
          <AMenuItem @click="$emit('rates')">费率配置</AMenuItem>
          <AMenuItem :disabled="busy || !model.enabled" @click="$emit('test')">测试连接</AMenuItem>
          <AMenuItem @click="$emit('control', model.enabled ? 'disable' : 'enable')">{{
            model.enabled ? '停用' : '启用'
          }}</AMenuItem>
          <AMenuItem
            v-if="model.enabled"
            @click="$emit('control', model.published ? 'unpublish' : 'publish')"
            >{{ model.published ? '取消发布' : '发布' }}</AMenuItem
          >
          <AMenuItem v-if="!model.enabled" danger @click="$emit('control', 'delete')"
            >删除模型</AMenuItem
          >
        </AMenu>
      </template>
    </ADropdown>
  </div>
</template>

<style scoped>
.model-actions,
.desktop-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}
.desktop-actions :deep(.ant-btn) {
  height: 28px;
  padding-inline: 5px;
  font-size: 13px;
}
@media (max-width: 760px) {
  .desktop-actions {
    display: none;
  }
}
</style>
