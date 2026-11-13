<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <main class="app-container skill-management-page">
    <WorkspaceToolbar title="技能">
      <template #actions>
        <RefreshButton label="刷新技能" :loading="loading" @click="load" />
        <a-button
          v-if="canUpload && activeTab === 'mine'"
          type="primary"
          aria-label="上传技能"
          @click="openUpload()"
        >
          <template #icon><UploadOutlined /></template>上传技能
        </a-button>
      </template>
    </WorkspaceToolbar>

    <section class="skill-content">
      <ATabs :active-key="activeTab" class="skill-tabs" aria-label="技能分类" @change="changeTab">
        <ATabPane key="market" tab="市场" />
        <ATabPane key="mine" tab="我的" />
      </ATabs>
      <p class="skill-intro">
        {{
          activeTab === 'market'
            ? '平台预制技能，启用后可用于对话和智能体。'
            : '上传技能包，管理自己的技能。'
        }}
      </p>
      <div class="skill-controls">
        <a-input
          v-model:value="query.keyword"
          allow-clear
          placeholder="搜索技能"
          @press-enter="search"
        >
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-select
          :value="query.status"
          aria-label="筛选技能状态"
          :options="statusOptions"
          placeholder="全部状态"
          allow-clear
          @change="changeStatus"
        />
        <span class="skill-total">共 {{ total }} 个</span>
      </div>

      <WorkspaceState v-if="loading && !skills.length" kind="loading" />
      <WorkspaceState
        v-else-if="error && !skills.length"
        kind="error"
        :message="error"
        @retry="load"
      />
      <WorkspaceState
        v-else-if="!skills.length"
        kind="empty"
        :title="
          query.status || query.keyword.trim()
            ? '没有符合条件的技能'
            : activeTab === 'market'
              ? '暂无预制技能'
              : '暂无个人技能'
        "
      />
      <template v-else>
        <a-alert v-if="error" class="stale-notice" type="warning" :message="error" show-icon />
        <div class="skill-grid">
          <article v-for="skill in skills" :key="skill.skillId" class="skill-item">
            <button
              type="button"
              class="skill-item__open"
              :aria-label="skill.skillName"
              :aria-describedby="`skill-description-${skill.skillId}`"
              @click="openDetail(skill)"
            >
              <SkillIdentityIcon :slug="skill.skillSlug" />
              <span class="skill-item__text">
                <strong :title="skill.skillName">{{ skill.skillName }}</strong>
                <span
                  :id="`skill-description-${skill.skillId}`"
                  class="skill-item__description"
                  :class="{ 'skill-item__error': failures[skill.skillId] }"
                  :role="failures[skill.skillId] ? 'alert' : undefined"
                  :title="failures[skill.skillId]?.message || skill.description"
                  >{{ failures[skill.skillId]?.message || skill.description || '暂无说明' }}</span
                >
              </span>
            </button>
            <a-switch
              :checked="skill.status === 'enabled'"
              :aria-label="`启用技能“${skill.skillName}”`"
              :loading="pending[skill.skillId] ?? false"
              :disabled="!canToggleSkill(skill) || pending[skill.skillId] || deleting"
              size="small"
              @change="toggleStatus(skill)"
            />
          </article>
        </div>
        <a-pagination
          v-if="total > query.pageSize"
          v-model:current="query.pageNum"
          :page-size="query.pageSize"
          :total="total"
          :show-size-changer="false"
          size="small"
          @change="load"
        />
      </template>
    </section>

    <SkillUploadModal
      v-model="uploadOpen"
      :can-manage="canManage"
      :target="uploadTarget"
      @installed="installed"
    />
    <SkillDetailModal
      :model-value="detailOpen"
      :skill="detail"
      :content="content"
      :loading="detailLoading"
      :error="detailError"
      :saving="saving"
      :deleting="deleting"
      :action-error="actionError"
      :action-details="actionDetails"
      :can-manage="canManage"
      :can-toggle="!!detail && canToggleSkill(detail)"
      :can-update="canUseCapability('skills', 'update')"
      :can-delete="canUseCapability('skills', 'delete')"
      @update:model-value="setDetailOpen"
      @retry="loadDetail"
      @toggle="toggleSelected"
      @update="updateSelected"
      @delete="removeSelected"
    />
  </main>
</template>

<script setup lang="ts">
import { canUseCapability } from '../../identity/public.ts'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Pagination as APagination,
  Select as ASelect,
  Switch as ASwitch,
  Tabs as ATabs,
  TabPane as ATabPane
} from 'ant-design-vue'
import { SearchOutlined, UploadOutlined } from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import SkillIdentityIcon from '../components/SkillIdentityIcon.vue'
import SkillDetailModal from '../components/SkillDetailModal.vue'
import SkillUploadModal from '../components/SkillUploadModal.vue'
import { useSkillsPage } from '../composables/useSkillsPage.ts'

const statusOptions = [
  { label: '已启用', value: 'enabled' },
  { label: '已停用', value: 'disabled' }
]
const {
  activeTab,
  changeTab,
  skills,
  total,
  loading,
  error,
  query,
  canManage,
  canUpload,
  canToggleSkill,
  pending,
  failures,
  detailOpen,
  detail,
  content,
  detailLoading,
  detailError,
  saving,
  deleting,
  actionError,
  actionDetails,
  uploadOpen,
  uploadTarget,
  load,
  search,
  changeStatus,
  openDetail,
  setDetailOpen,
  toggleStatus,
  toggleSelected,
  openUpload,
  updateSelected,
  installed,
  loadDetail,
  removeSelected
} = useSkillsPage()
</script>

<style scoped lang="scss">
.skill-management-page {
  display: flex;
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex-direction: column;
  color: var(--color-text);
  background: var(--bg-page);
  font-family: var(--workspace-font-family);
}
.skill-content {
  container-type: inline-size;
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px 28px 32px;
}
.skill-tabs {
  margin-bottom: 8px;
}
.skill-intro {
  margin: 0 0 22px;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.skill-controls {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}
.skill-controls :deep(.ant-input-affix-wrapper) {
  width: min(360px, 42%);
}
.skill-controls :deep(.ant-select) {
  width: 132px;
}
.skill-total {
  margin-left: auto;
  color: var(--color-text-secondary);
  font-size: 13px;
}
.skill-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px 28px;
  align-content: start;
}
@container (min-width: 1360px) {
  .skill-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
@container (max-width: 960px) {
  .skill-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@container (max-width: 560px) {
  .skill-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .skill-controls {
    flex-wrap: wrap;
  }
  .skill-controls :deep(.ant-input-affix-wrapper) {
    width: 100%;
  }
}
.skill-item {
  display: flex;
  min-width: 0;
  min-height: 88px;
  align-items: center;
  gap: 16px;
  padding: 10px 12px;
  border-radius: 10px;
}
.skill-item:hover {
  background: var(--gray-25);
}
.skill-item__open {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 14px;
  padding: 4px 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.skill-item__open:focus-visible {
  outline: 2px solid var(--main-400);
  outline-offset: 4px;
}
.skill-item__text {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}
.skill-item__text strong,
.skill-item__description {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-item__text strong {
  color: var(--color-text);
  font-size: 15px;
  font-weight: 500;
  line-height: 22px;
}
.skill-item__description {
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 20px;
}
.skill-item__error {
  color: var(--color-error);
}
.skill-item :deep(.ant-switch) {
  flex-shrink: 0;
}
.stale-notice {
  margin-bottom: 16px;
}
:deep(.ant-pagination) {
  justify-content: flex-end;
  margin-top: 24px;
}
</style>
