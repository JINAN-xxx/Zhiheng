<script setup lang="ts">
import {
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import { EllipsisOutlined } from '@ant-design/icons-vue'
import type { ChatModel } from '../api/index.ts'

const props = withDefaults(
  defineProps<{
    model: ChatModel
    busy: boolean
    testing: boolean
    canWrite: boolean
    canReadRates: boolean
    canTest: boolean
    presentation?: 'list' | 'card'
    testDisabled?: boolean
  }>(),
  { presentation: 'list', testDisabled: false }
)
const emit = defineEmits<{
  edit: []
  rates: []
  test: []
  control: [action: 'enable' | 'disable' | 'publish' | 'unpublish' | 'delete']
}>()
function edit(): void {
  if (props.canWrite) emit('edit')
}
function rates(): void {
  if (props.canReadRates) emit('rates')
}
function test(): void {
  if (props.canTest && !props.busy && props.model.enabled && !props.testDisabled) emit('test')
}
function control(action: 'enable' | 'disable' | 'publish' | 'unpublish' | 'delete'): void {
  if (props.canWrite) emit('control', action)
}
</script>

<template>
  <div
    v-if="canWrite || canReadRates || canTest"
    class="model-actions"
    :class="{ 'model-actions--card': presentation === 'card' }"
  >
    <AButton
      v-if="presentation === 'card' && canTest"
      type="link"
      size="small"
      aria-label="测试连接"
      :loading="testing && busy"
      :disabled="busy || !model.enabled || testDisabled"
      @click="test"
      >测试</AButton
    >
    <div v-if="presentation === 'list'" class="desktop-actions">
      <AButton v-if="canWrite" type="link" size="small" aria-label="编辑模型" @click="edit"
        >编辑</AButton
      >
      <AButton v-if="canReadRates" type="link" size="small" aria-label="费率配置" @click="rates"
        >费率</AButton
      >
      <AButton
        v-if="canTest"
        type="link"
        size="small"
        aria-label="测试连接"
        :loading="testing && busy"
        :disabled="busy || !model.enabled || testDisabled"
        @click="test"
        >测试</AButton
      >
    </div>
    <ADropdown v-if="presentation === 'list' || canWrite || canReadRates" :trigger="['click']">
      <AButton type="text" aria-label="更多操作"><EllipsisOutlined /></AButton>
      <template #overlay>
        <AMenu>
          <AMenuItem v-if="canWrite" @click="edit">编辑模型</AMenuItem>
          <AMenuItem v-if="canReadRates" @click="rates">费率配置</AMenuItem>
          <AMenuItem
            v-if="presentation === 'list' && canTest"
            :disabled="busy || !model.enabled || testDisabled"
            @click="test"
            >测试连接</AMenuItem
          >
          <AMenuItem v-if="canWrite" @click="control(model.enabled ? 'disable' : 'enable')">{{
            model.enabled ? '停用' : '启用'
          }}</AMenuItem>
          <AMenuItem
            v-if="canWrite && model.enabled"
            @click="control(model.published ? 'unpublish' : 'publish')"
            >{{ model.published ? '取消发布' : '发布' }}</AMenuItem
          >
          <AMenuItem v-if="canWrite" danger @click="control('delete')">删除模型</AMenuItem>
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
.model-actions--card {
  flex-shrink: 0;
  gap: 6px;
}
.model-actions--card > :deep(.ant-btn) {
  height: 28px;
  padding-inline: 6px;
  font-size: 13px;
}
@media (max-width: 760px) {
  .desktop-actions {
    display: none;
  }
}
</style>
