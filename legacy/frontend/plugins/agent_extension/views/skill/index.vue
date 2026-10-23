<template>
  <main class="app-container skill-management-page">
    <WorkspaceToolbar title="技能管理">
      <template #actions>
        <a-tooltip title="刷新技能" placement="bottom">
          <a-button shape="circle" :loading="loading" aria-label="刷新技能" @click="load">
            <template #icon><ReloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-button v-if="can('agent_extension:skill:add')" type="primary" @click="uploadOpen = true">
          <template #icon><UploadOutlined /></template>
          上传技能
        </a-button>
      </template>
    </WorkspaceToolbar>

    <section class="skill-content">
      <div class="skill-controls">
        <a-input v-model:value="query.keyword" allow-clear placeholder="搜索技能" @press-enter="search">
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-select v-model:value="query.status" :options="statusOptions" placeholder="全部状态" allow-clear @change="search" />
        <span class="skill-total">共 {{ total }} 个</span>
      </div>

      <WorkspaceState v-if="loading && !skills.length" kind="loading" />
      <WorkspaceState v-else-if="error && !skills.length" kind="error" :message="error" @retry="load" />
      <WorkspaceState v-else-if="!skills.length" kind="empty" title="暂无技能" />
      <template v-else>
        <a-alert v-if="error" class="stale-notice" type="warning" message="刷新失败，当前技能可能已过期" show-icon />
        <div class="section-title">平台技能</div>
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
            <div class="skill-card__footer">
              <a-button
                v-if="can('agent_extension:skill:query')"
                type="link"
                size="small"
                @click="openDetail(skill)"
              >查看</a-button>
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

    <SkillUploadModal v-model="uploadOpen" @installed="handleInstalled" />
    <SkillDetailDrawer
      v-model="detailOpen"
      :skill-id="selectedSkillId"
      :permissions="permissions"
      @changed="handleChanged"
      @deleted="handleDeleted"
    />
  </main>
</template>

<script setup name="AgentExtensionSkill">
import { ReloadOutlined, SearchOutlined, ThunderboltOutlined, UploadOutlined } from '@ant-design/icons-vue'
import useUserStore from '@/store/modules/user'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import WorkspaceStatus from '@/components/Workspace/WorkspaceStatus.vue'
import WorkspaceToolbar from '@/components/Workspace/WorkspaceToolbar.vue'
import { listSkills } from '../../api/skill.js'
import { hasSkillPermission, normalizeSkillPage, skillStatusLabel } from '../../utils/skill.js'
import SkillDetailDrawer from './components/SkillDetailDrawer.vue'
import SkillUploadModal from './components/SkillUploadModal.vue'

const userStore = useUserStore()
const permissions = computed(() => userStore.permissions || [])
const can = (permission) => hasSkillPermission(permissions.value, permission)
const skills = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const uploadOpen = ref(false)
const detailOpen = ref(false)
const selectedSkillId = ref('')
const query = reactive({ keyword: '', status: undefined, pageNum: 1, pageSize: 12 })
const statusOptions = [
  { label: '已启用', value: 'enabled' },
  { label: '已停用', value: 'disabled' },
]
let requestGeneration = 0

async function load() {
  const generation = ++requestGeneration
  loading.value = true
  error.value = ''
  try {
    const page = normalizeSkillPage(await listSkills(query))
    if (generation !== requestGeneration) return
    skills.value = page.rows
    total.value = page.total
  } catch (reason) {
    if (generation === requestGeneration) error.value = normalizeErrorMessage(reason, '技能列表加载失败')
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

function search() {
  query.pageNum = 1
  load()
}

function openDetail(skill) {
  selectedSkillId.value = skill.skillId
  detailOpen.value = true
}

function handleInstalled() {
  query.pageNum = 1
  load()
}

function handleChanged(changed) {
  const index = skills.value.findIndex((skill) => skill.skillId === changed.skillId)
  if (index >= 0) skills.value[index] = changed
}

function handleDeleted(skillId) {
  skills.value = skills.value.filter((skill) => skill.skillId !== skillId)
  total.value = Math.max(0, total.value - 1)
  load()
}

onMounted(load)
onBeforeUnmount(() => { requestGeneration += 1 })
</script>

<style scoped lang="scss">
.skill-management-page { display: flex; min-width: 0; min-height: 0; height: 100%; flex-direction: column; color: var(--color-text); background: var(--bg-container); font-family: var(--workspace-font-family); }
.skill-content { min-height: 0; flex: 1; overflow: auto; padding: 20px 24px 28px; }
.skill-controls { display: flex; min-height: 54px; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--bg-container); }
.skill-controls :deep(.ant-input-affix-wrapper) { width: min(360px, 42%); }
.skill-controls :deep(.ant-select) { width: 132px; }
.skill-total { margin-left: auto; color: var(--color-text-secondary); font-size: 13px; }
.section-title { margin: 22px 1px 12px; color: var(--color-text); font-size: 15px; font-weight: 600; }
.skill-grid { display: grid; grid-template-columns: repeat(3, minmax(230px, 1fr)); gap: 14px; }
.skill-card { display: flex; min-height: 154px; flex-direction: column; padding: 17px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--bg-container); }
.skill-card:hover { border-color: var(--main-300); }
.skill-card__header { display: flex; min-width: 0; align-items: center; gap: 11px; }
.skill-card__header strong { min-width: 0; flex: 1; overflow: hidden; color: var(--color-text); font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
.skill-card__icon { display: grid; width: 36px; height: 36px; flex: 0 0 36px; place-items: center; border-radius: 7px; color: var(--main-700); background: var(--main-50); font-size: 17px; }
.skill-card p { display: -webkit-box; min-height: 42px; margin: 14px 0 10px; overflow: hidden; color: var(--color-text-secondary); font-size: 13px; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.skill-card__footer { display: flex; justify-content: flex-end; margin-top: auto; padding-top: 8px; border-top: 1px solid var(--gray-100); }
.stale-notice { margin-top: 12px; }
:deep(.ant-pagination) { justify-content: flex-end; margin-top: 18px; }
@media (max-width: 1080px) { .skill-grid { grid-template-columns: repeat(2, minmax(230px, 1fr)); } }
</style>

