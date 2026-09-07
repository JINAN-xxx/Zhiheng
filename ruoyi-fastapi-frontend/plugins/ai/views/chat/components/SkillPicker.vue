<template>
  <a-popover v-model:open="open" trigger="click" placement="topLeft" overlay-class-name="chat-skill-picker">
    <template #content>
      <div class="skill-picker-panel">
        <div class="skill-picker-header">
          <strong>选择技能</strong>
          <span>最多选择 5 个技能</span>
        </div>
        <a-input v-model:value="keyword" allow-clear placeholder="搜索技能" size="small">
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <div class="skill-picker-list">
          <WorkspaceState v-if="loading" kind="loading" />
          <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="load" />
          <WorkspaceState v-else-if="!filteredSkills.length" kind="empty" title="暂无可用技能" />
          <template v-else>
            <button
              v-for="skill in filteredSkills"
              :key="skill.skillSlug"
              type="button"
              class="skill-option"
              :class="{ 'is-selected': selectedSlugs.has(skill.skillSlug) }"
              @click="toggle(skill)"
            >
              <span class="skill-option__icon"><ThunderboltOutlined /></span>
              <span class="skill-option__body"><strong>{{ skill.skillName }}</strong><small>{{ skill.description || '暂无说明' }}</small></span>
              <CheckOutlined v-if="selectedSlugs.has(skill.skillSlug)" class="skill-option__check" />
            </button>
          </template>
        </div>
        <div class="skill-picker-footer">
          <span>已选择 {{ modelValue.length }} 个</span>
          <a-button type="link" size="small" @click="open = false">完成</a-button>
        </div>
      </div>
    </template>
    <a-tooltip title="选择技能">
      <a-button type="text" :disabled="disabled" aria-label="选择技能">@</a-button>
    </a-tooltip>
  </a-popover>
</template>

<script setup>
import { CheckOutlined, SearchOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import feedback from '@/plugins/antdFeedback'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import WorkspaceState from '@/components/Workspace/WorkspaceState.vue'
import { listAvailableSkills } from '../../../../agent_extension/api/skill.js'
import { normalizeAvailableSkills } from '../../../../agent_extension/utils/skill.js'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  open: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'update:open', 'selected'])
const skills = ref([])
const keyword = ref('')
const loading = ref(false)
const error = ref('')
const loaded = ref(false)
const open = computed({ get: () => props.open, set: (value) => emit('update:open', value) })
const selectedSlugs = computed(() => new Set(props.modelValue.map((skill) => skill.skillSlug)))
const filteredSkills = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  if (!query) return skills.value
  return skills.value.filter((skill) => `${skill.skillName} ${skill.description}`.toLowerCase().includes(query))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    skills.value = normalizeAvailableSkills(await listAvailableSkills())
    loaded.value = true
  } catch (reason) {
    error.value = normalizeErrorMessage(reason, '技能列表加载失败')
  } finally {
    loading.value = false
  }
}

function toggle(skill) {
  if (selectedSlugs.value.has(skill.skillSlug)) {
    emit('update:modelValue', props.modelValue.filter((item) => item.skillSlug !== skill.skillSlug))
    return
  }
  if (props.modelValue.length >= 5) {
    feedback.msgWarning('每条消息最多选择 5 个技能')
    return
  }
  emit('update:modelValue', [...props.modelValue, skill])
  emit('selected', skill)
}

watch(open, (visible) => {
  if (visible && !loaded.value && !loading.value) load()
})
</script>

<style scoped>
.skill-picker-panel { width: 340px; color: var(--color-text); }
.skill-picker-header, .skill-picker-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.skill-picker-header { margin-bottom: 10px; }
.skill-picker-header span, .skill-picker-footer { color: var(--color-text-secondary); font-size: 12px; }
.skill-picker-list { min-height: 150px; max-height: 280px; margin-top: 10px; overflow: auto; border: 1px solid var(--gray-150); border-radius: 6px; background: var(--bg-container); }
.skill-picker-list :deep(.workspace-state) { min-height: 148px; padding: 16px; }
.skill-option { display: flex; width: 100%; min-height: 58px; align-items: center; gap: 10px; padding: 9px 10px; border: 0; border-bottom: 1px solid var(--gray-100); background: var(--bg-container); color: var(--color-text); text-align: left; cursor: pointer; }
.skill-option:last-child { border-bottom: 0; }
.skill-option:hover { background: var(--gray-50); }
.skill-option.is-selected { background: var(--main-50); }
.skill-option__icon { display: grid; width: 30px; height: 30px; flex: 0 0 30px; place-items: center; border-radius: 6px; color: var(--main-700); background: var(--main-50); }
.skill-option__body { display: grid; min-width: 0; flex: 1; gap: 2px; }
.skill-option__body strong, .skill-option__body small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.skill-option__body small { color: var(--color-text-secondary); font-size: 12px; }
.skill-option__check { color: var(--main-700); }
.skill-picker-footer { min-height: 34px; padding-top: 6px; }
</style>
