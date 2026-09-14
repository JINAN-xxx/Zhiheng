<template>
  <section class="work-member-bar" aria-label="工作成员">
    <div class="work-member-bar__list">
      <div v-for="member in members" :key="member.agentId" class="work-member">
        <div class="work-member__name" :title="member.agentNameSnapshot">
          <RobotOutlined />
          <span>{{ member.agentNameSnapshot }}</span>
        </div>
        <a-tag :color="member.available ? 'blue' : 'error'">v{{ member.publishedVersion }}{{ member.available ? '' : ' · 不可用' }}</a-tag>
        <a-tooltip v-if="canManage && member.latestPublishedVersion > member.publishedVersion" title="确认后升级冻结版本">
          <a-button type="text" size="small" :disabled="locked" @click="emit('upgrade', member)">升级</a-button>
        </a-tooltip>
        <a-button v-if="canEdit" type="text" danger size="small" :disabled="locked || members.length <= 1" aria-label="移除智能体" @click="emit('remove', member)">
          <DeleteOutlined />
        </a-button>
      </div>
    </div>
    <a-button v-if="canManage" :disabled="locked || members.length >= 10" @click="emit('add')"><PlusOutlined />添加智能体</a-button>
  </section>
</template>

<script setup>
import { DeleteOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons-vue'
defineProps({
  members: { type: Array, default: () => [] },
  canManage: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
  locked: { type: Boolean, default: false },
})
const emit = defineEmits(['add', 'remove', 'upgrade'])
</script>

<style scoped>
.work-member-bar { min-width: 0; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 12px 20px; border-bottom: 1px solid var(--gray-150); background: var(--bg-container); }
.work-member-bar__list { min-width: 0; display: flex; flex: 1; flex-wrap: wrap; gap: 8px; }
.work-member { min-width: 0; max-width: 100%; display: flex; align-items: center; gap: 4px; padding: 3px 4px 3px 8px; border: 1px solid var(--gray-150); border-radius: var(--workspace-radius-sm); }
.work-member__name { min-width: 0; max-width: 220px; display: flex; align-items: center; gap: 6px; }
.work-member__name span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 600px) { .work-member-bar { padding: 10px 12px; flex-direction: column; } .work-member__name { max-width: min(160px, 42vw); } }
</style>
