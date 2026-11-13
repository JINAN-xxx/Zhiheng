<script setup lang="ts">
import { computed } from 'vue'
import {
  Alert as AAlert,
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem,
  Modal as AModal,
  Switch as ASwitch
} from 'ant-design-vue'
import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  EllipsisOutlined,
  ExperimentOutlined
} from '@ant-design/icons-vue'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import type { ChatModel } from '../api/index.ts'
import { modelTypeLabel } from '../presentation.ts'
import ModelIdentityIcon from './ModelIdentityIcon.vue'

const props = defineProps<{
  modelValue: boolean
  model: ChatModel | null
  connectionLabel: string
  connectionIssue: string
  error: string
  errorDetails?: unknown
  canWrite: boolean
  canUpdate?: boolean | undefined
  canDelete?: boolean | undefined
  canToggle?: boolean | undefined
  canReadRates: boolean
  canTest: boolean
  canReadConnections: boolean
  pending: boolean
  testBusy: boolean
  testing: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  toggle: []
  copy: []
  edit: []
  rates: []
  test: []
  trial: []
  control: [action: 'delete']
}>()

const testDisabled = computed(
  () => props.testBusy || props.pending || !props.model?.enabled || Boolean(props.connectionIssue)
)

function toggle(): void {
  if (props.model && (props.canToggle ?? props.canWrite) && !props.pending) emit('toggle')
}
function edit(): void {
  if (props.model && (props.canUpdate ?? props.canWrite) && !props.pending) emit('edit')
}
function rates(): void {
  if (props.model && props.canReadRates && !props.pending) emit('rates')
}
function test(): void {
  if (props.model && props.canTest && !testDisabled.value) emit('test')
}
function trial(): void {
  if (props.model && props.canTest && !testDisabled.value) emit('trial')
}
function control(action: 'delete'): void {
  if (!props.model || !(props.canDelete ?? props.canWrite) || props.pending) return
  emit('control', action)
}
</script>

<template>
  <AModal
    :open="modelValue"
    :width="680"
    centered
    :mask-closable="!pending"
    :closable="!pending"
    destroy-on-close
    wrap-class-name="model-detail-modal"
    @cancel="emit('update:modelValue', false)"
  >
    <template #title>
      <div class="detail-header">
        <ModelIdentityIcon v-if="model" class="detail-icon" :type="model.model_type" />
        <div class="detail-heading">
          <h2 :title="model?.name">{{ model?.name || '模型详情' }}</h2>
          <ASwitch
            v-if="model && (canToggle ?? canWrite)"
            :checked="model.enabled"
            :aria-label="`启用模型“${model.name}”`"
            :loading="pending"
            :disabled="pending"
            @change="toggle"
          />
        </div>
        <p
          v-if="model"
          class="detail-description"
          :title="canReadConnections ? connectionLabel : undefined"
        >
          {{ modelTypeLabel(model.model_type) }}模型<template
            v-if="canReadConnections && connectionLabel"
          >
            · {{ connectionLabel }}</template
          >
        </p>
      </div>
    </template>

    <template v-if="model">
      <dl class="detail-fields">
        <div class="detail-field detail-field--id">
          <dt>模型 ID</dt>
          <dd>
            <code>{{ model.id }}</code>
            <AButton
              class="detail-copy"
              type="text"
              size="small"
              aria-label="复制模型代码"
              :disabled="!model.id.trim()"
              @click="emit('copy')"
            >
              <CopyOutlined aria-hidden="true" />复制模型代码
            </AButton>
          </dd>
        </div>
        <div class="detail-field">
          <dt>模型类型</dt>
          <dd>{{ modelTypeLabel(model.model_type) }}模型</dd>
        </div>
        <div class="detail-field">
          <dt>启用状态</dt>
          <dd>{{ model.enabled ? '已启用' : '已停用' }}</dd>
        </div>
        <div class="detail-field">
          <dt>上游模型</dt>
          <dd>
            <code>{{ model.upstream_model }}</code>
          </dd>
        </div>
        <template v-if="model.model_type === 'chat'">
          <div class="detail-field">
            <dt>上下文窗口</dt>
            <dd>
              {{
                model.context_window_tokens == null
                  ? '使用平台输入预算'
                  : `${model.context_window_tokens.toLocaleString()} Token`
              }}
            </dd>
          </div>
          <div class="detail-field">
            <dt>最大输出</dt>
            <dd>{{ model.max_output_tokens.toLocaleString() }} Token</dd>
          </div>
          <div class="detail-field">
            <dt>图片支持</dt>
            <dd>{{ model.vision_enabled ? '支持' : '不支持' }}</dd>
          </div>
        </template>
        <div v-else class="detail-field">
          <dt>模型用途</dt>
          <dd>
            {{
              model.model_type === 'embedding'
                ? '用于文本向量化与知识检索'
                : '用于检索结果相关性排序'
            }}
          </dd>
        </div>
        <div v-if="canReadConnections" class="detail-field">
          <dt>连接</dt>
          <dd>{{ connectionLabel || '未找到连接' }}</dd>
        </div>
      </dl>
      <AAlert
        v-if="canReadConnections && connectionIssue"
        class="detail-notice"
        message="连接异常"
        :description="connectionIssue"
        type="warning"
        show-icon
      />
      <AAlert v-if="error" class="detail-notice" :message="error" type="error" show-icon />
      <ResourceReferences :details="errorDetails" />
    </template>

    <template #footer>
      <div v-if="model" class="detail-footer">
        <AButton
          v-if="canTest"
          aria-label="测试连接"
          :loading="testing && testBusy"
          :disabled="testDisabled"
          @click="test"
        >
          <ExperimentOutlined aria-hidden="true" />测试连接
        </AButton>
        <AButton
          v-if="canTest"
          type="primary"
          aria-label="试用模型"
          :disabled="testDisabled"
          @click="trial"
        >
          <ExperimentOutlined aria-hidden="true" />试用模型
        </AButton>
        <ADropdown v-if="canWrite || canReadRates" :trigger="['click']" :disabled="pending">
          <AButton type="text" class="detail-more" aria-label="更多模型操作" :disabled="pending">
            <EllipsisOutlined aria-hidden="true" />
          </AButton>
          <template #overlay>
            <AMenu>
              <AMenuItem v-if="canUpdate ?? canWrite" key="edit" :disabled="pending" @click="edit">
                <EditOutlined aria-hidden="true" /> 编辑模型
              </AMenuItem>
              <AMenuItem v-if="canReadRates" key="rates" :disabled="pending" @click="rates">
                费率配置
              </AMenuItem>
              <AMenuItem
                v-if="canDelete ?? canWrite"
                key="delete"
                danger
                :disabled="pending"
                @click="control('delete')"
              >
                <DeleteOutlined aria-hidden="true" /> 删除模型
              </AMenuItem>
            </AMenu>
          </template>
        </ADropdown>
      </div>
    </template>
  </AModal>
</template>

<style scoped>
.detail-icon {
  margin-bottom: 18px;
}
.detail-heading {
  display: flex;
  min-width: 0;
  min-height: 28px;
  align-items: center;
  gap: 12px;
}
.detail-heading h2 {
  display: -webkit-box;
  min-width: 0;
  flex: 1;
  margin: 0;
  overflow: hidden;
  color: var(--color-text);
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.detail-heading :deep(.ant-switch) {
  flex-shrink: 0;
}
.detail-description {
  display: -webkit-box;
  margin: 6px 0 0;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 13px;
  font-weight: 400;
  line-height: 1.7;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.detail-fields {
  min-width: 0;
  margin: 0;
}
.detail-field {
  display: grid;
  min-width: 0;
  grid-template-columns: 100px minmax(0, 1fr);
  gap: 20px;
  padding: 13px 0;
  border-bottom: 1px solid var(--gray-150);
  font-size: 14px;
  line-height: 1.7;
}
.detail-field:first-child {
  padding-top: 0;
}
.detail-field:last-child {
  border-bottom: 0;
}
.detail-field dt {
  color: var(--color-text-secondary);
  font-weight: 400;
}
.detail-field dd {
  min-width: 0;
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}
.detail-field code {
  font-family: var(--workspace-font-mono);
  font-size: 13px;
  white-space: normal;
  overflow-wrap: anywhere;
}
.detail-field--id dd {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.detail-field--id code {
  min-width: 0;
  flex: 1;
}
.detail-copy {
  height: 24px;
  flex-shrink: 0;
  padding-inline: 4px;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.detail-notice {
  margin-top: 16px;
}
.detail-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.detail-more {
  display: grid;
  width: 32px;
  height: 32px;
  padding: 0;
  place-items: center;
  color: var(--color-text-secondary);
  font-size: 20px;
}
:global(.model-detail-modal .ant-modal) {
  max-width: calc(100vw - 32px);
  padding-bottom: 0;
}
:global(.model-detail-modal .ant-modal-content) {
  display: flex;
  max-height: calc(100vh - 40px);
  flex-direction: column;
  padding: 24px;
  border-radius: 12px;
  background: var(--bg-elevated, #fff);
}
:global(.model-detail-modal .ant-modal-header) {
  flex-shrink: 0;
  margin-bottom: 20px;
  background: var(--bg-elevated, #fff);
}
:global(.model-detail-modal .ant-modal-body) {
  min-width: 0;
  min-height: 0;
  flex: 1;
  overflow: auto;
  overscroll-behavior: contain;
}
:global(.model-detail-modal .ant-modal-footer) {
  flex-shrink: 0;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--gray-150);
}
</style>
