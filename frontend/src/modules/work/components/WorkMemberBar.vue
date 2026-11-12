<!-- Adapted from original Work UI, MIT licensed. -->
<template>
  <section class="work-member-bar" aria-label="工作成员">
    <div class="work-member-bar__list">
      <div v-for="member in members" :key="member.agentId" class="work-member">
        <div class="work-member__name" :title="member.agentNameSnapshot">
          <RobotOutlined />
          <span>{{ member.agentNameSnapshot }}</span>
        </div>
        <a-tag v-if="!member.available" color="error">不可用</a-tag>
        <a-tooltip
          v-if="canManage && (member.latestPublishedVersion ?? 0) > member.publishedVersion"
          title="确认后升级冻结版本"
        >
          <a-button
            aria-label="升级"
            type="text"
            size="small"
            :disabled="locked"
            @click="emit('upgrade', member)"
            >升级</a-button
          >
        </a-tooltip>
        <a-button
          v-if="canEdit"
          type="text"
          danger
          size="small"
          :disabled="locked || members.length <= 1"
          aria-label="移除智能体"
          @click="emit('remove', member)"
        >
          <DeleteOutlined />
        </a-button>
      </div>
    </div>
    <a-button
      v-if="canManage"
      aria-label="添加智能体"
      :disabled="locked || members.length >= 10"
      @click="emit('add')"
      ><PlusOutlined />添加智能体</a-button
    >
  </section>
</template>

<script setup lang="ts">
import { DeleteOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons-vue'
import { Button as AButton, Tag as ATag, Tooltip as ATooltip } from 'ant-design-vue'
import type { WorkMember } from '../api/index.ts'
defineProps<{ members: WorkMember[]; canManage: boolean; canEdit: boolean; locked: boolean }>()
const emit = defineEmits<{ add: []; remove: [member: WorkMember]; upgrade: [member: WorkMember] }>()
</script>

<style scoped>
.work-member-bar {
  width: min(1040px, 100%);
  min-width: 0;
  flex-shrink: 0;
  align-self: center;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 24px;
}
.work-member-bar__list {
  min-width: 0;
  max-width: 100%;
  max-height: 132px;
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 8px;
  overflow-y: auto;
  padding: 2px;
}
.work-member {
  min-width: 0;
  max-width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 5px 4px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--bg-container);
  font-size: 13px;
}
.work-member__name {
  min-width: 0;
  max-width: 220px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.work-member__name span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-member__name :deep(.anticon) {
  flex-shrink: 0;
  color: var(--main-color);
}
.work-member :deep(.ant-tag) {
  margin: 0;
  padding: 0 5px;
  border-radius: 5px;
  font-size: var(--workspace-font-size-caption);
}
.work-member-bar > :deep(.ant-btn) {
  flex-shrink: 0;
  border-color: var(--gray-200);
  border-radius: 9px;
  background: var(--bg-container);
  box-shadow: none;
  font-size: 13px;
}
@media (max-width: 600px) {
  .work-member-bar {
    padding: 10px 12px;
    flex-direction: column;
  }
  .work-member__name {
    max-width: min(160px, 42vw);
  }
}
</style>
