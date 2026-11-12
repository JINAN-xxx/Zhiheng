<template>
  <span
    class="skill-identity-icon"
    :class="[`skill-identity-icon--${tone}`, { 'skill-identity-icon--plain': plain }]"
    aria-hidden="true"
  >
    <component :is="icon" />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  BarChartOutlined,
  CodeOutlined,
  FileTextOutlined,
  ThunderboltOutlined
} from '@ant-design/icons-vue'

const props = defineProps<{ slug: string; plain?: boolean }>()
const tones = ['violet', 'blue', 'teal', 'amber', 'green', 'rose'] as const
const tone = computed(() => {
  let hash = 0
  for (const letter of props.slug) hash = (hash * 31 + letter.charCodeAt(0)) >>> 0
  return tones[hash % tones.length]
})
const icon = computed(() => {
  if (props.slug === 'document-summary') return FileTextOutlined
  if (props.slug === 'code-review') return CodeOutlined
  if (props.slug === 'data-analysis') return BarChartOutlined
  return ThunderboltOutlined
})
</script>

<style scoped>
.skill-identity-icon {
  display: grid;
  width: 48px;
  height: 48px;
  flex: 0 0 48px;
  place-items: center;
  border: 1px solid color-mix(in srgb, currentColor 12%, transparent);
  border-radius: 50%;
  background: color-mix(in srgb, currentColor 5%, white);
  font-size: 23px;
}
.skill-identity-icon--violet {
  color: #8054e8;
}
.skill-identity-icon--blue {
  color: #4286f5;
}
.skill-identity-icon--teal {
  color: #16acc1;
}
.skill-identity-icon--amber {
  color: #e8a01a;
}
.skill-identity-icon--green {
  color: #51a445;
}
.skill-identity-icon--rose {
  color: #f16f76;
}
.skill-identity-icon--plain {
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  border: 0;
  border-radius: 0;
  background: transparent;
  font-size: 20px;
}
</style>
