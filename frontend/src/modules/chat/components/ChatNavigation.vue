<!-- Adapted from original RecentSessions.vue, MIT licensed. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Input as AInput,
  Spin as ASpin,
  Empty as AEmpty,
  RadioGroup as ARadioGroup,
  RadioButton as ARadioButton
} from 'ant-design-vue'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { useChatNavigation } from '../composables/workspace.ts'
import type { Conversation } from '../api/index.ts'
const emit = defineEmits<{ navigate: [] }>()
const { conversations, selected, loading, error, refresh, select } = useChatNavigation()
function openConversation(conversation: Conversation): void {
  void select(conversation)
  emit('navigate')
}
const query = ref('')
const filtered = computed(() =>
  conversations.value.filter((item) =>
    item.title.toLocaleLowerCase().includes(query.value.toLocaleLowerCase())
  )
)
</script>
<template>
  <section class="recent-sessions" aria-label="对话列表">
    <div class="recent-sessions__heading">
      <ARadioGroup value="active" size="small" aria-label="会话状态"
        ><ARadioButton value="active">最近会话</ARadioButton
        ><ARadioButton value="archived" disabled
          ><span title="当前版本暂不支持归档">已归档</span></ARadioButton
        ></ARadioGroup
      ><button
        type="button"
        class="recent-sessions__refresh"
        aria-label="刷新会话"
        @click="refresh"
      >
        <ReloadOutlined />
      </button>
    </div>
    <AInput v-model:value="query" allow-clear placeholder="搜索当前页会话" aria-label="搜索最近会话"
      ><template #prefix><SearchOutlined /></template
    ></AInput>
    <p v-if="error" class="recent-sessions__error" role="alert">{{ error }}</p>
    <ASpin :spinning="loading" wrapper-class-name="recent-sessions__loading">
      <div class="recent-sessions__list">
        <div
          v-for="conversation in filtered"
          :key="conversation.id"
          class="recent-session"
          :class="{ active: selected?.id === conversation.id }"
        >
          <button
            type="button"
            class="recent-session__open conversation-item"
            :aria-label="conversation.title"
            :aria-current="selected?.id === conversation.id ? 'page' : undefined"
            @click="openConversation(conversation)"
          >
            <strong>{{ conversation.title }}</strong
            ><small>{{
              new Date(conversation.created_at).toLocaleString('zh-CN', {
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
              })
            }}</small>
          </button>
        </div>
        <AEmpty
          v-if="!loading && !filtered.length"
          :description="query ? '当前页没有匹配会话' : '暂无会话'"
          :image="AEmpty.PRESENTED_IMAGE_SIMPLE"
        /></div
    ></ASpin>
  </section>
</template>
<style scoped lang="scss">
.recent-sessions {
  min-height: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 9px;
  padding: 13px 10px;
  color: var(--color-text);
}
.recent-sessions__heading {
  display: flex;
  min-height: 28px;
  align-items: center;
  justify-content: space-between;
  color: var(--color-text-secondary);
  font-size: 12px;
  font-weight: 600;
}
.recent-sessions__heading :deep(.ant-radio-button-wrapper) {
  padding-inline: 9px;
  font-size: 12px;
}
.recent-sessions__pagination {
  align-self: center;
}
.recent-sessions__refresh {
  display: inline-flex;
  width: 30px;
  height: 30px;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
}
.recent-sessions__stale,
.recent-sessions__error {
  margin: 0;
  padding: 8px;
  border-radius: var(--workspace-radius-sm);
  font-size: 12px;
}
.recent-sessions__stale {
  color: var(--workspace-warning);
  background: var(--workspace-warning-soft);
}
.recent-sessions__error {
  color: var(--workspace-danger);
  background: var(--workspace-danger-soft);
}
.recent-sessions__list {
  min-height: 0;
  height: 100%;
  flex: 1;
  overflow-y: auto;
}
.recent-sessions__loading {
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
.recent-sessions__loading :deep(.ant-spin-container) {
  height: 100%;
}
.recent-session {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 3px;
  padding: 7px 6px 7px 9px;
  border-radius: var(--workspace-radius-sm);
}
.recent-session:hover {
  background: var(--gray-50);
}
.recent-session.active {
  background: var(--main-50);
  color: var(--main-700);
}
.recent-session__open {
  min-width: 0;
  display: grid;
  flex: 1;
  gap: 3px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.recent-session__open strong,
.recent-session__open small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.recent-session__open strong {
  font-size: 13px;
  font-weight: 500;
}
.recent-session__open small {
  color: var(--color-text-secondary);
  font-size: var(--workspace-font-size-caption);
  line-height: var(--workspace-line-height);
  font-variant-numeric: tabular-nums;
}
.recent-session.active .recent-session__open small {
  color: var(--color-text-secondary);
}
.recent-sessions :deep(.ant-input-affix-wrapper) {
  border-color: var(--gray-150);
  background: var(--bg-container);
  box-shadow: none;
}
.recent-sessions :deep(.ant-input-affix-wrapper input),
.recent-sessions :deep(.ant-input-prefix),
.recent-sessions :deep(.ant-input-clear-icon) {
  color: var(--color-text);
  background: transparent;
}
.recent-sessions :deep(.ant-empty-description) {
  color: var(--color-text-secondary);
}
.recent-session :deep(.workspace-more-menu) {
  opacity: 0;
}
.recent-session:hover :deep(.workspace-more-menu),
.recent-session:focus-within :deep(.workspace-more-menu) {
  opacity: 1;
}
@media (hover: none) {
  .recent-session :deep(.workspace-more-menu) {
    opacity: 1;
  }
}
</style>
