<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <a-modal
    :open="modelValue"
    :width="680"
    centered
    :mask-closable="!busy"
    :closable="!busy"
    destroy-on-close
    wrap-class-name="skill-detail-modal workspace-scroll-dialog"
    @cancel="emit('update:modelValue', false)"
  >
    <template #title>
      <div class="detail-header">
        <SkillIdentityIcon v-if="skill" class="detail-icon" :slug="skill.skillSlug" />
        <div class="detail-heading">
          <h2 :title="skill?.skillName">{{ skill?.skillName || '技能详情' }}</h2>
          <a-switch
            v-if="skill && (canToggle ?? canManage)"
            :checked="skill.status === 'enabled'"
            :aria-label="`启用技能“${skill.skillName}”`"
            :loading="saving"
            :disabled="operationsDisabled"
            @change="emit('toggle')"
          />
        </div>
        <p v-if="skill?.description" class="detail-description" :title="skill.description">
          {{ skill.description }}
        </p>
      </div>
    </template>

    <WorkspaceState v-if="loading" kind="loading" />
    <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="skill">
      <section class="skill-markdown" aria-label="技能内容">
        <MarkdownRender
          :content="markdownContent || '暂无内容'"
          html-policy="safe"
          :is-dark="false"
          :final="true"
          mode="chat"
          code-renderer="shiki"
          :code-block-props="{ monacoOptions: { fontSize: 13, lineHeight: 22 } }"
          @copy="copyMarkdownCode"
        />
      </section>
      <AAlert
        v-if="actionError"
        class="detail-action-error"
        :message="actionError"
        type="error"
        show-icon
      />
      <ResourceReferences :details="actionDetails" />
    </template>

    <template #footer>
      <div
        v-if="
          skill &&
          ((canUpdate ?? canManage) || (canDelete ?? canManage)) &&
          skill.sourceType === 'upload'
        "
        class="detail-footer"
      >
        <a-dropdown :trigger="['click']" :disabled="operationsDisabled">
          <a-button
            type="text"
            class="detail-more"
            aria-label="更多技能操作"
            :disabled="operationsDisabled"
          >
            <EllipsisOutlined aria-hidden="true" />
          </a-button>
          <template #overlay>
            <a-menu>
              <a-menu-item
                v-if="canUpdate ?? canManage"
                key="update"
                :disabled="operationsDisabled"
                @click="emit('update')"
              >
                <UploadOutlined aria-hidden="true" /> 替换技能
              </a-menu-item>
              <a-menu-item
                v-if="canDelete ?? canManage"
                key="delete"
                danger
                :disabled="operationsDisabled"
                @click="emit('delete')"
              >
                <DeleteOutlined aria-hidden="true" /> 删除技能
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </div>
    </template>
  </a-modal>
</template>

<script setup lang="ts">
import { copyMarkdownCode } from '../../../shared/markdownClipboard.ts'
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
import { DeleteOutlined, EllipsisOutlined, UploadOutlined } from '@ant-design/icons-vue'
import { MarkdownRender } from 'markstream-vue'
import 'markstream-vue/index.css'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import type { Skill } from '../api/index.ts'
import SkillIdentityIcon from './SkillIdentityIcon.vue'

const props = defineProps<{
  modelValue: boolean
  skill: Skill | null
  content: string
  loading: boolean
  error: string
  saving: boolean
  deleting: boolean
  actionError: string
  actionDetails?: unknown
  canManage: boolean
  canUpdate?: boolean | undefined
  canDelete?: boolean | undefined
  canToggle?: boolean | undefined
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  retry: []
  toggle: []
  update: []
  delete: []
}>()
const busy = computed(() => props.saving || props.deleting)
const operationsDisabled = computed(
  () => busy.value || props.loading || Boolean(props.error) || !props.skill
)
const markdownContent = computed(() =>
  props.content.replace(/^\uFEFF?---[ \t]*\r?\n[\s\S]*?\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/, '')
)
</script>

<style scoped>
.detail-icon {
  margin-bottom: 18px;
}
.detail-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 28px;
}
.detail-heading h2 {
  display: -webkit-box;
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--color-text);
  font-size: 18px;
  font-weight: 600;
  line-height: 1.5;
  overflow-wrap: anywhere;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.detail-heading :deep(.ant-switch) {
  flex-shrink: 0;
}
.detail-more {
  display: grid;
  width: 28px;
  height: 28px;
  padding: 0;
  place-items: center;
  color: var(--color-text-secondary);
  font-size: 20px;
}
.detail-description {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  overflow: hidden;
  margin: 6px 0 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 400;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.skill-markdown {
  box-sizing: border-box;
  min-width: 0;
  min-height: 180px;
  padding: 20px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--color-text);
  font-size: 15px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.skill-markdown :deep(.markstream-vue) {
  --ms-font-sans: var(--workspace-font-family);
  --ms-font-mono: var(--workspace-font-mono);
  --markstream-code-font-family: var(--workspace-font-mono);
  --ms-text-body: 15px;
  --ms-leading-body: 1.8;
  --ms-text-h1: 22px;
  --ms-text-h2: 18px;
  --ms-text-h3: 16px;
  --ms-leading-h1: 1.45;
  --ms-leading-h2: 1.5;
  --ms-leading-h3: 1.5;
  --ms-flow-heading-1-mt: 16px;
  --ms-flow-heading-1-mb: 10px;
  --ms-flow-heading-2-mt: 16px;
  --ms-flow-heading-2-mb: 10px;
  --ms-flow-heading-3-mt: 16px;
  --ms-flow-heading-3-mb: 10px;
  --ms-flow-heading-4-mt: 16px;
  --ms-flow-heading-4-mb: 10px;
  --ms-flow-heading-5-mt: 16px;
  --ms-flow-heading-5-mb: 10px;
  --ms-flow-heading-6-mt: 16px;
  --ms-flow-heading-6-mb: 10px;
  --ms-flow-paragraph-y: 12px;
  --ms-flow-list-y: 12px;
  --ms-flow-list-item-y: 4px;
}
.detail-footer {
  display: flex;
  justify-content: flex-end;
}
.detail-action-error {
  margin-top: 16px;
}
:global(.skill-detail-modal .ant-modal-content) {
  border-radius: 12px;
  background: var(--bg-elevated);
}
:global(.skill-detail-modal .ant-modal-header) {
  margin-bottom: 20px;
  background: var(--bg-elevated);
}
:global(.skill-detail-modal .ant-modal-body) {
  max-height: 440px;
}
@media (max-height: 520px) {
  .detail-header {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    column-gap: 10px;
  }
  .detail-icon {
    width: 34px;
    height: 34px;
    grid-row: span 2;
    margin: 0;
    font-size: 18px;
  }
  .detail-heading,
  .detail-description {
    grid-column: 2;
  }
  .detail-description {
    margin-top: 2px;
    -webkit-line-clamp: 1;
  }
  :global(.skill-detail-modal .ant-modal-header) {
    margin-bottom: 12px;
  }
}
</style>
