<!-- Adapted from original skill UI, MIT licensed. -->
<template>
  <section v-if="open" class="skill-picker-panel" aria-label="选择技能" @keydown="handleKeydown">
    <div class="skill-picker-header">
      <div class="skill-picker-heading">
        <strong>我的技能</strong>
        <span>已选 {{ modelValue.length }} / 5</span>
      </div>
      <label class="skill-picker-search">
        <SearchOutlined aria-hidden="true" />
        <input
          ref="searchInput"
          v-model="keyword"
          type="text"
          role="combobox"
          aria-label="搜索技能"
          aria-autocomplete="list"
          aria-expanded="true"
          :aria-controls="listId"
          :aria-activedescendant="activeSkill ? optionId(activeSkill.skillSlug) : undefined"
          placeholder="搜索技能"
          autocomplete="off"
        />
      </label>
      <button
        type="button"
        class="skill-picker-close"
        aria-label="收起技能选择"
        title="收起技能选择（Esc）"
        @click="dismiss"
      >
        <CloseOutlined aria-hidden="true" />
      </button>
    </div>
    <div class="skill-picker-results">
      <WorkspaceState v-if="loading" kind="loading" />
      <WorkspaceState v-else-if="error" kind="error" :message="error" @retry="load" />
      <WorkspaceState
        v-else-if="!filteredSkills.length"
        kind="empty"
        :title="keyword.trim() ? '没有匹配的技能' : '暂无可用技能'"
      />
      <div
        :id="listId"
        ref="listElement"
        class="skill-picker-list"
        role="listbox"
        aria-label="可用技能"
        aria-multiselectable="true"
      >
        <template v-if="!loading && !error">
          <button
            v-for="(skill, index) in filteredSkills"
            :id="optionId(skill.skillSlug)"
            :key="skill.skillSlug"
            type="button"
            role="option"
            tabindex="-1"
            class="skill-option"
            :class="{
              'is-selected': selectedSlugs.has(skill.skillSlug),
              'is-active': index === activeIndex
            }"
            :aria-label="skill.skillName"
            :aria-describedby="`${optionId(skill.skillSlug)}-description`"
            :aria-selected="selectedSlugs.has(skill.skillSlug)"
            @focus="activeIndex = index"
            @click="toggle(skill)"
          >
            <SkillIdentityIcon :slug="skill.skillSlug" plain />
            <strong :title="skill.skillName">{{ skill.skillName }}</strong>
            <span
              :id="`${optionId(skill.skillSlug)}-description`"
              class="skill-option__description"
              :title="skill.description"
              >{{ skill.description || '暂无说明' }}</span
            >
            <span class="skill-option__check" aria-hidden="true">
              <CheckOutlined v-if="selectedSlugs.has(skill.skillSlug)" />
            </span>
          </button>
        </template>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { message } from 'ant-design-vue'
import { CheckOutlined, CloseOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { errorMessage, isAborted, RequestScope } from '../../../shared/http.ts'
import WorkspaceState from '../../../shared/components/WorkspaceState.vue'
import SkillIdentityIcon from './SkillIdentityIcon.vue'
import { listAvailableSkills } from '../api/index.ts'
import type { Skill } from '../api/index.ts'
const props = defineProps<{ modelValue: Skill[]; open: boolean; disabled: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: Skill[]]
  'update:open': [value: boolean]
  selected: [skill: Skill]
  dismiss: []
}>()
const skills = ref<Skill[]>([]),
  keyword = ref(''),
  loading = ref(false),
  error = ref(''),
  activeIndex = ref(0)
const searchInput = ref<HTMLInputElement | null>(null)
const listElement = ref<HTMLElement | null>(null)
const listId = `chat-skills-${useId()}`
const optionId = (slug: string) => `${listId}-${slug}`
const scope = new RequestScope()
const selectedSlugs = computed(() => new Set(props.modelValue.map((skill) => skill.skillSlug)))
const filteredSkills = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return skills.value.filter((skill) =>
    `${skill.skillName} ${skill.description}`.toLowerCase().includes(text)
  )
})
const activeSkill = computed(() =>
  loading.value || error.value ? undefined : filteredSkills.value[activeIndex.value]
)
async function load(): Promise<void> {
  const context = scope.replace()
  loading.value = true
  error.value = ''
  try {
    const result = await listAvailableSkills(context.signal)
    if (context.isCurrent() && props.open && !props.disabled) skills.value = result
  } catch (reason) {
    if (context.isCurrent() && !isAborted(reason)) error.value = errorMessage(reason)
  } finally {
    if (context.isCurrent()) loading.value = false
  }
}
function toggle(skill: Skill): void {
  if (props.disabled || !props.open) return
  if (selectedSlugs.value.has(skill.skillSlug)) {
    emit(
      'update:modelValue',
      props.modelValue.filter((item) => item.skillSlug !== skill.skillSlug)
    )
    return
  }
  if (props.modelValue.length >= 5) {
    void message.warning('每条消息最多选择 5 个技能')
    return
  }
  emit('update:modelValue', [...props.modelValue, skill])
  emit('selected', skill)
}
function dismiss(): void {
  emit('update:open', false)
  emit('dismiss')
}
function handleKeydown(event: KeyboardEvent): boolean {
  if (!props.open || event.isComposing || event.keyCode === 229) return false
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    dismiss()
    return true
  }
  if (
    event.target instanceof Element &&
    event.target.closest('.skill-picker-close, .workspace-state')
  )
    return false
  if (!['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) return false
  event.preventDefault()
  event.stopPropagation()
  if (props.disabled || loading.value || error.value || !filteredSkills.value.length) return true
  if (event.key === 'Enter') {
    if (activeSkill.value) toggle(activeSkill.value)
  } else {
    const count = filteredSkills.value.length
    activeIndex.value = (activeIndex.value + (event.key === 'ArrowDown' ? 1 : -1) + count) % count
    void nextTick(() => {
      listElement.value
        ?.querySelectorAll<HTMLElement>('.skill-option')
        [activeIndex.value]?.scrollIntoView({ block: 'nearest' })
    })
  }
  return true
}
watch(filteredSkills, () => {
  activeIndex.value = 0
})
watch(
  () => props.open,
  (visible) => {
    if (visible && !props.disabled) {
      void load()
      void nextTick(() => {
        if (props.open && !props.disabled) searchInput.value?.focus()
      })
    } else {
      scope.replace()
      loading.value = false
      error.value = ''
      skills.value = []
      keyword.value = ''
    }
  },
  { immediate: true }
)
watch(
  () => props.disabled,
  (disabled) => {
    if (disabled) emit('update:open', false)
  }
)
onBeforeUnmount(() => scope.dispose())
defineExpose({ handleKeydown })
</script>

<style scoped>
.skill-picker-panel {
  display: flex;
  min-height: 0;
  flex: 0 1 auto;
  flex-direction: column;
  border-bottom: 1px solid var(--gray-150);
  padding: 14px var(--chat-composer-padding, 20px) 10px;
  color: var(--color-text);
}
.skill-picker-header,
.skill-picker-heading,
.skill-picker-search {
  display: flex;
  min-width: 0;
  align-items: center;
}
.skill-picker-header {
  flex-shrink: 0;
  gap: 12px;
  margin-bottom: 8px;
}
.skill-picker-heading {
  gap: 12px;
  margin-right: auto;
  white-space: nowrap;
}
.skill-picker-heading strong {
  font-size: 14px;
  font-weight: 500;
}
.skill-picker-heading span {
  color: var(--color-text-tertiary);
  font-size: 12px;
}
.skill-picker-search {
  width: min(260px, 40%);
  gap: 8px;
  padding: 7px 10px;
  border-radius: 8px;
  background: var(--gray-25);
  color: var(--color-text-tertiary);
}
.skill-picker-search:focus-within {
  outline: 1px solid var(--main-200);
}
.skill-picker-search input {
  min-width: 0;
  width: 100%;
  border: 0;
  outline: none;
  padding: 0;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  font-size: 14px;
}
.skill-picker-search input::placeholder {
  color: var(--color-text-tertiary);
}
.skill-picker-close {
  display: grid;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}
.skill-picker-close:hover {
  background: var(--gray-50);
}
.skill-picker-results {
  min-height: 0;
  overflow-y: auto;
  max-height: 280px;
}
.skill-picker-results :deep(.workspace-state) {
  min-height: 100px;
  padding: 12px;
}
.skill-option {
  display: flex;
  width: 100%;
  min-height: 44px;
  align-items: center;
  gap: 12px;
  padding: 9px 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--color-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.skill-option:hover,
.skill-option.is-active {
  background: var(--gray-50);
}
.skill-option.is-selected {
  background: var(--main-30);
}
.skill-option strong {
  min-width: 0;
  max-width: 32%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 15px;
  font-weight: 500;
}
.skill-option__description {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
  font-size: 14px;
}
.skill-option__check {
  width: 16px;
  flex: 0 0 16px;
  color: var(--main-700);
}
@media (max-width: 560px) {
  .skill-picker-header {
    flex-wrap: wrap;
    gap: 8px;
  }
  .skill-picker-search {
    order: 1;
    width: 100%;
  }
  .skill-option {
    gap: 8px;
  }
}
</style>
