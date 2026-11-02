<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <main class="app-container skill-management-page">
    <WorkspaceToolbar title="我的技能">
      <template #actions>
        <a-tooltip title="刷新技能" placement="bottom">
          <a-button :loading="loading" aria-label="刷新技能" @click="load">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button v-if="canManage" type="primary" aria-label="上传技能" @click="uploadOpen = true">
          <template #icon><UploadOutlined /></template>
          上传技能
        </a-button>
      </template>
    </WorkspaceToolbar>

    <section class="skill-content">
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
          :options="statusOptions"
          placeholder="全部状态"
          allow-clear
          @update:value="changeStatus"
          @change="search"
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
      <WorkspaceState v-else-if="!skills.length" kind="empty" title="暂无技能" />
      <template v-else>
        <a-alert
          v-if="error"
          class="stale-notice"
          type="warning"
          message="刷新失败，当前技能可能已过期"
          show-icon
        />
        <div class="skill-grid">
          <article v-for="skill in skills" :key="skill.skillId" class="skill-card">
            <div class="skill-card__header">
              <span class="skill-card__icon"><ThunderboltOutlined /></span>
              <strong :title="skill.skillName">{{ skill.skillName }}</strong>
              <WorkspaceStatus
                :status="skill.status === 'enabled' ? 'active' : 'warning'"
                :label="skillStatusLabel(skill.status)"
              />
            </div>
            <p>{{ skill.description || '暂无说明' }}</p>
            <div class="skill-card__tags">
              <span>{{ skill.sourceType === 'builtin' ? '平台内置' : '上传技能' }}</span
              ><span>v{{ skill.version }}</span>
            </div>
            <div class="skill-card__footer">
              <span>{{ skill.fileCount }} 个文件 · {{ formatSkillBytes(skill.totalBytes) }}</span>
              <a-button type="link" size="small" @click="openDetail(skill)">查看详情</a-button>
            </div>
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

    <SkillUploadModal v-model="uploadOpen" :can-manage="canManage" @installed="handleInstalled" />
    <SkillDetailDrawer
      v-model="detailOpen"
      :skill-id="selectedSkillId"
      :can-manage="canManage"
      @changed="handleChanged"
      @deleted="handleDeleted"
    />
  </main>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Alert as AAlert,
  Button as AButton,
  Input as AInput,
  Pagination as APagination,
  Select as ASelect,
  Tooltip as ATooltip
} from 'ant-design-vue'
import {
  ReloadOutlined,
  SearchOutlined,
  ThunderboltOutlined,
  UploadOutlined
} from '@ant-design/icons-vue'
import { identity } from '../../identity/public.ts'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import WorkspaceStatus from '../../../shared/components/WorkspaceStatus.vue'
import WorkspaceToolbar from '../../../shared/components/WorkspaceToolbar.vue'
import { listSkills } from '../api/index.ts'
import type { Skill } from '../api/index.ts'
import { formatSkillBytes, skillStatusLabel } from '../presentation.ts'
import SkillDetailDrawer from '../components/SkillDetailDrawer.vue'
import SkillUploadModal from '../components/SkillUploadModal.vue'
const route = useRoute(),
  router = useRouter()
const canManage = computed(() => identity.value !== null)
const skills = ref<Skill[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const uploadOpen = ref(false),
  detailOpen = ref(false),
  selectedSkillId = ref('')
const query = reactive({
  keyword: '',
  status: undefined as string | undefined,
  pageNum: 1,
  pageSize: 12
})
const statusOptions = [
  { label: '已启用', value: 'enabled' },
  { label: '已停用', value: 'disabled' }
]
const scope = new RequestScope()
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const page = await listSkills(query, context.signal)
    if (context.isCurrent()) {
      skills.value = page.rows
      total.value = page.total
    }
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function search(): void {
  query.pageNum = 1
  void load()
}
function changeStatus(value: unknown): void {
  query.status = value === 'enabled' || value === 'disabled' ? value : undefined
}
function openDetail(skill: Skill): void {
  selectedSkillId.value = skill.skillId
  detailOpen.value = true
}
function handleInstalled(): void {
  query.pageNum = 1
  void load()
}
function handleChanged(changed: Skill): void {
  skills.value = skills.value.map((skill) => (skill.skillId === changed.skillId ? changed : skill))
}
function handleDeleted(): void {
  void load()
}
watch(
  [() => route.query.action, canManage],
  ([action, allowed]) => {
    if (action !== 'upload' || !allowed) return
    uploadOpen.value = true
    const query = { ...route.query }
    delete query.action
    void router.replace({ query })
  },
  { immediate: true }
)
onMounted(load)
onBeforeUnmount(() => scope.dispose())
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
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 24px 28px 32px;
}
.skill-controls {
  display: flex;
  min-height: 40px;
  align-items: center;
  gap: 10px;
  margin-bottom: 22px;
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
  grid-template-columns: repeat(3, minmax(230px, 1fr));
  gap: 20px;
}
.skill-card {
  display: flex;
  min-height: 225px;
  flex-direction: column;
  padding: 22px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--bg-container);
}
.skill-card:hover {
  border-color: var(--main-300);
}
.skill-card__header {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 11px;
}
.skill-card__header strong {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: var(--color-text);
  font-size: 16px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-card__icon {
  display: grid;
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  place-items: center;
  border-radius: 7px;
  color: var(--main-700);
  background: var(--main-50);
  font-size: 17px;
}
.skill-card p {
  display: -webkit-box;
  min-height: 42px;
  margin: 14px 0 10px;
  overflow: hidden;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.skill-card__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid var(--gray-100);
}
.skill-card__tags {
  display: flex;
  gap: 7px;
  margin-bottom: 18px;
}
.skill-card__tags span {
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--gray-50);
  color: var(--color-text-secondary);
  font-size: 12px;
}
.skill-card__tags span:first-child {
  background: var(--main-50);
  color: var(--main-700);
}
.skill-card__footer > span {
  color: var(--color-text-secondary);
  font-size: 12px;
}
.stale-notice {
  margin-top: 12px;
}
:deep(.ant-pagination) {
  justify-content: flex-end;
  margin-top: 18px;
}
@media (max-width: 1080px) {
  .skill-grid {
    grid-template-columns: repeat(2, minmax(230px, 1fr));
  }
}
@media (max-width: 600px) {
  .skill-content {
    padding: 14px 12px;
  }
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
:deep(.ant-btn-link) {
  color: var(--main-700);
}
</style>
