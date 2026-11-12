<script setup lang="ts">
import { computed } from 'vue'
import {
  ArrowLeftOutlined,
  BookOutlined,
  MoreOutlined,
  PlusOutlined,
  SettingOutlined,
  TeamOutlined
} from '@ant-design/icons-vue'
import {
  Button as AButton,
  Dropdown as ADropdown,
  Menu as AMenu,
  MenuItem as AMenuItem
} from 'ant-design-vue'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import { identity } from '../../identity/public.ts'
import type { KnowledgeBase } from '../api/index.ts'
const props = defineProps<{
  database: KnowledgeBase
  loading: boolean
  busy: boolean
  graphAvailable: boolean
}>()
const emit = defineEmits<{
  back: []
  refresh: []
  settings: []
  acl: []
  upload: []
  rebuild: []
  graph: []
  migration: []
  remove: []
}>()
const owner = computed(
  () => props.database.can_manage && props.database.owner_id === identity.value?.id
)
const sharing = computed(() => (props.database.sharing_scope === 'users' ? '成员共享' : '仅自己'))
</script>
<template>
  <header class="knowledge-header">
    <button type="button" class="knowledge-back" aria-label="返回知识库" @click="emit('back')">
      <ArrowLeftOutlined />知识库
    </button>
    <div class="knowledge-heading-row">
      <div class="knowledge-heading">
        <span class="knowledge-heading-icon" aria-hidden="true"><BookOutlined /></span>
        <div class="knowledge-heading-copy">
          <h1 :title="database.name">{{ database.name }}</h1>
          <p>
            <span
              v-if="database.description"
              class="knowledge-description"
              :title="database.description"
              >{{ database.description }}</span
            ><span class="knowledge-sharing"><TeamOutlined />{{ sharing }}</span>
          </p>
        </div>
      </div>
      <div class="knowledge-header-actions">
        <RefreshButton label="刷新资料" :loading="loading" @click="emit('refresh')" />
        <AButton
          v-if="owner"
          type="text"
          :disabled="busy"
          aria-label="知识库设置"
          @click="emit('settings')"
          ><SettingOutlined /><span>设置</span></AButton
        >
        <AButton
          v-if="owner && database.document_policy === 'library'"
          type="text"
          aria-label="共享"
          @click="emit('acl')"
          ><TeamOutlined /><span>共享</span></AButton
        >
        <AButton
          v-if="owner"
          type="primary"
          :disabled="busy"
          aria-label="添加资料"
          @click="emit('upload')"
          ><PlusOutlined />添加资料</AButton
        >
        <ADropdown v-if="owner" :trigger="['click']">
          <AButton type="text" class="knowledge-more" aria-label="更多操作"
            ><MoreOutlined
          /></AButton>
          <template #overlay
            ><AMenu>
              <AMenuItem key="settings" :disabled="busy" @click="emit('settings')"
                >知识库设置</AMenuItem
              >
              <AMenuItem key="rebuild" :disabled="busy" @click="emit('rebuild')"
                >重新处理全部资料</AMenuItem
              >
              <AMenuItem key="graph" :disabled="!graphAvailable" @click="emit('graph')"
                >高级：知识图谱</AMenuItem
              >
              <AMenuItem
                v-if="database.document_policy !== 'library'"
                key="migration"
                :disabled="busy"
                @click="emit('migration')"
                >迁移资料与共享</AMenuItem
              >
              <AMenuItem key="delete" :disabled="busy" danger @click="emit('remove')"
                >删除知识库</AMenuItem
              >
            </AMenu></template
          >
        </ADropdown>
      </div>
    </div>
  </header>
</template>
<style scoped>
.knowledge-header {
  flex: 0 0 auto;
  padding-bottom: 18px;
}
.knowledge-back {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0;
  margin: 0 0 17px;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.knowledge-back:hover {
  color: var(--main-color);
}
.knowledge-heading-row,
.knowledge-heading,
.knowledge-header-actions {
  display: flex;
  align-items: center;
}
.knowledge-heading-row {
  justify-content: space-between;
  gap: 24px;
  min-height: 48px;
}
.knowledge-heading {
  min-width: 0;
  gap: 12px;
}
.knowledge-heading-icon {
  display: grid;
  place-items: center;
  flex: 0 0 42px;
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--main-30);
  color: var(--main-color);
  font-size: 21px;
}
.knowledge-heading-copy {
  min-width: 0;
}
.knowledge-heading h1 {
  margin: 0;
  color: var(--color-text);
  font-size: 21px;
  font-weight: 600;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.knowledge-heading p {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin: 5px 0 0;
  color: var(--color-text-secondary);
  font-size: 12px;
}
.knowledge-description {
  max-width: 440px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.knowledge-sharing {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.knowledge-header-actions {
  flex: 0 0 auto;
  gap: 5px;
}
.knowledge-header-actions :deep(.ant-btn) {
  height: 34px;
  border-radius: 7px;
  font-size: 13px;
  box-shadow: none;
}
.knowledge-header-actions :deep(.workspace-refresh-button) {
  width: 32px;
  min-width: 32px;
  flex-basis: 32px;
  border-color: transparent;
  background: transparent;
}
.knowledge-header-actions :deep(.ant-btn-text) {
  color: var(--color-text-secondary);
}
.knowledge-header-actions :deep(.ant-btn-primary) {
  margin-left: 7px;
  padding: 0 14px;
}
.knowledge-header-actions .knowledge-more {
  width: 30px;
  padding: 0;
}
.knowledge-back:focus-visible {
  outline: 2px solid var(--main-300);
  outline-offset: 4px;
  border-radius: 3px;
}
@media (max-width: 760px) {
  .knowledge-heading-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 16px;
  }
  .knowledge-header-actions {
    width: 100%;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
  .knowledge-heading {
    max-width: 100%;
  }
  .knowledge-description {
    max-width: 240px;
  }
  .knowledge-heading h1 {
    font-size: 19px;
  }
  .knowledge-header {
    padding-bottom: 10px;
  }
}
</style>
