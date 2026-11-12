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
    mutating?: boolean
    testDisabled?: boolean
  }>(),
  { mutating: false, testDisabled: false }
)
const emit = defineEmits<{
  edit: []
  rates: []
  test: []
  trial: []
  control: [action: 'enable' | 'disable' | 'delete']
}>()
function edit(): void {
  if (props.canWrite && !props.mutating) emit('edit')
}
function rates(): void {
  if (props.canReadRates && !props.mutating) emit('rates')
}
function test(): void {
  if (props.canTest && !props.busy && !props.mutating && props.model.enabled && !props.testDisabled)
    emit('test')
}
function trial(): void {
  if (props.canTest && !props.busy && !props.mutating && props.model.enabled && !props.testDisabled)
    emit('trial')
}
function control(action: 'enable' | 'disable' | 'delete'): void {
  if (props.canWrite && !props.mutating) emit('control', action)
}
</script>

<template>
  <div v-if="canWrite || canReadRates || canTest" class="model-actions">
    <div class="desktop-actions">
      <AButton
        v-if="canWrite"
        type="link"
        size="small"
        aria-label="编辑模型"
        :disabled="mutating"
        @click="edit"
        >编辑</AButton
      >
      <AButton
        v-if="canReadRates"
        type="link"
        size="small"
        aria-label="费率配置"
        :disabled="mutating"
        @click="rates"
        >费率</AButton
      >
      <AButton
        v-if="canTest"
        type="link"
        size="small"
        aria-label="测试连接"
        :loading="testing && busy"
        :disabled="busy || mutating || !model.enabled || testDisabled"
        @click="test"
        >测试</AButton
      >
    </div>
    <ADropdown :trigger="['click']" :disabled="mutating">
      <AButton type="text" aria-label="更多操作" :disabled="mutating"><EllipsisOutlined /></AButton>
      <template #overlay>
        <AMenu>
          <AMenuItem v-if="canWrite" :disabled="mutating" @click="edit">编辑模型</AMenuItem>
          <AMenuItem v-if="canReadRates" :disabled="mutating" @click="rates">费率配置</AMenuItem>
          <AMenuItem
            v-if="canTest"
            :disabled="busy || mutating || !model.enabled || testDisabled"
            @click="test"
            >测试连接</AMenuItem
          >
          <AMenuItem
            v-if="canTest"
            :disabled="busy || mutating || !model.enabled || testDisabled"
            @click="trial"
            >试用模型</AMenuItem
          >
          <AMenuItem
            v-if="canWrite"
            :disabled="mutating"
            @click="control(model.enabled ? 'disable' : 'enable')"
            >{{ model.enabled ? '停用' : '启用' }}</AMenuItem
          >
          <AMenuItem v-if="canWrite" danger :disabled="mutating" @click="control('delete')"
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
