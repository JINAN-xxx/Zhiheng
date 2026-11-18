<script setup lang="ts">
import { computed } from 'vue'
import { CloseOutlined } from '@ant-design/icons-vue'
import { SkillIdentityIcon } from '../../skills/public.ts'
import type { WorkOptions } from '../api/index.ts'

const props = defineProps<{
  slugs: string[]
  options: WorkOptions | null
  disabled: boolean
}>()
const emit = defineEmits<{ remove: [slug: string] }>()
const selected = computed(() =>
  props.slugs.map((slug) => ({
    slug,
    name: props.options?.skills.find((skill) => skill.slug === slug)?.name ?? slug
  }))
)
</script>

<template>
  <div v-if="selected.length" class="work-selected-skills" aria-label="已选技能">
    <span v-for="skill in selected" :key="skill.slug" class="work-selected-skill">
      <SkillIdentityIcon :slug="skill.slug" plain />
      <span class="work-selected-skill__name" :title="skill.name">{{ skill.name }}</span>
      <button
        type="button"
        :aria-label="`移除技能：${skill.name}`"
        :disabled="disabled"
        @click="emit('remove', skill.slug)"
      >
        <CloseOutlined aria-hidden="true" />
      </button>
    </span>
  </div>
</template>

<style scoped>
.work-selected-skills {
  display: flex;
  flex-wrap: wrap;
  flex-shrink: 0;
  gap: 8px 12px;
  margin: 0 0 12px;
}
.work-selected-skill {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 6px;
  color: var(--main-700);
  font-size: 15px;
}
.work-selected-skill__name {
  min-width: 0;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.work-selected-skill button {
  display: grid;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--color-text-tertiary);
  font-size: 12px;
  cursor: pointer;
}
.work-selected-skill button:hover {
  background: var(--main-50);
  color: var(--main-700);
}
.work-selected-skill button:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
