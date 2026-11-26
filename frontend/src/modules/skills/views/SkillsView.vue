<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <main class="app-container skill-management-page">
    <WorkspaceToolbar page title="技能">
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
        <ATabPane key="market" tab="平台技能" />
        <ATabPane key="mine" tab="我的技能" />
      </ATabs>
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
              ? '暂无平台技能'
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
                <span class="skill-item__heading">
                  <strong :title="skill.skillName">{{ skill.skillName }}</strong>
                  <span
                    v-if="skill.sourceType === 'upload' && skill.platformRevisionId"
                    class="skill-item__publication"
                    >已上架</span
                  >
                </span>
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
              v-if="skill.sourceType !== 'platform'"
              :checked="skill.status === 'enabled'"
              :aria-label="`启用技能“${skill.skillName}”`"
              :loading="pending[skill.skillId] ?? false"
              :disabled="!canToggleSkill(skill) || pending[skill.skillId] || deleting"
              size="small"
              @change="toggleStatus(skill)"
            />
            <WorkspaceMoreMenu
              v-if="publicationActions(skill).length"
              :items="publicationActions(skill)"
              @command="changePublication(skill, $event)"
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
      :can-manage="canManage"
      :can-toggle="!!detail && canToggleSkill(detail)"
      :can-update="canUseCapability('skills', 'update')"
      :can-delete="canUseCapability('skills', 'delete')"
      :publication-actions="detail ? publicationActions(detail) : []"
      :publishing="publishing"
      @update:model-value="setDetailOpen"
      @retry="loadDetail"
      @toggle="toggleSelected"
      @update="updateSelected"
      @delete="removeSelected"
      @publication="detail && changePublication(detail, $event)"
    />
    <a-modal
      :open="actionBlock !== null"
      :title="actionBlock?.title ?? ''"
      width="min(520px, calc(100vw - 32px))"
      :z-index="1100"
      centered
      @cancel="actionBlock = null"
    >
      <div class="skill-action-block">
        <p>{{ actionBlock?.message }}</p>
        <ResourceReferences :details="actionBlock?.details" />
      </div>
      <template #footer>
        <a-button type="primary" @click="actionBlock = null">关闭</a-button>
      </template>
    </a-modal>
  </main>
</template>

<script setup lang="ts">
import { canUseCapability } from '../../identity/public.ts'
import RefreshButton from '../../../shared/components/RefreshButton.vue'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Modal as AModal,
  Pagination as APagination,
  Select as ASelect,
  Switch as ASwitch,
  Tabs as ATabs,
  TabPane as ATabPane
} from 'ant-design-vue'
import { SearchOutlined, UploadOutlined } from '@ant-design/icons-vue'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import ResourceReferences from '../../../shared/components/ResourceReferences.vue'
import WorkspaceMoreMenu from '../../../shared/components/WorkspaceMoreMenu.vue'
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
  actionBlock,
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
  removeSelected,
  publishing,
  publicationActions,
  changePublication
} = useSkillsPage()
</script>

<style scoped lang="scss">
.skill-action-block {
  max-height: 50vh;
  overflow: auto;
  overflow-wrap: anywhere;
}

.skill-action-block > p {
  margin: 0;
}

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
  padding: 0 28px 28px;
}
.skill-tabs :deep(.ant-tabs-nav) {
  margin: 0 0 22px;
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
.skill-item__heading {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
}
.skill-item__publication {
  flex-shrink: 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
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
@media (max-width: 640px) {
  .skill-content {
    padding: 0 16px 16px;
  }
}
</style>
