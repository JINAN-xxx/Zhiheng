<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  title: string
  timestamp: string
  selected?: boolean
  disabled?: boolean
  openLabel?: string
  description?: string
}>()
const emit = defineEmits<{ open: [] }>()
const tooltip = computed(() =>
  props.description && props.description !== props.title
    ? props.title + '\n' + props.description
    : props.title
)
const timeLabel = computed(() =>
  Number.isNaN(Date.parse(props.timestamp))
    ? '刚刚'
    : new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      }).format(new Date(props.timestamp))
)
</script>
<template>
  <div class="history-record" :class="{ active: selected }">
    <button
      type="button"
      class="history-record__open"
      :aria-label="openLabel || title"
      :aria-current="selected ? 'page' : undefined"
      :title="tooltip"
      :disabled="disabled"
      @click="emit('open')"
    >
      <strong>{{ title }}</strong>
      <small class="history-record__meta">
        <time :datetime="timestamp">{{ timeLabel }}</time
        ><slot name="meta" />
      </small>
    </button>
    <slot name="actions" />
  </div>
</template>
