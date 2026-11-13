<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { FileTextOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { canUseCapability } from '../../identity/public.ts'
const props = defineProps<{ activeView: string; graphAvailable?: boolean }>()
const emit = defineEmits<{ navigate: [view: string] }>()
const root = ref<HTMLElement | null>(null),
  id = useId()
const items = computed(() => [
  { view: 'documents', label: '资料', icon: FileTextOutlined },
  ...(canUseCapability('knowledge')
    ? [{ view: 'query', label: '检索测试', icon: SearchOutlined }]
    : [])
])
function select(view: string) {
  if (props.activeView !== view) emit('navigate', view)
}
function keydown(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const current = Math.max(
    0,
    items.value.findIndex((item) => item.view === props.activeView)
  )
  const index =
    event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.value.length - 1
        : (current + (event.key === 'ArrowRight' ? 1 : -1) + items.value.length) %
          items.value.length
  const next = items.value[index]!
  root.value?.querySelectorAll<HTMLButtonElement>('button')[index]?.focus()
  select(next.view)
}
</script>
<template>
  <nav
    ref="root"
    class="knowledge-section-nav"
    aria-label="知识库功能导航"
    role="tablist"
    @keydown="keydown"
  >
    <button
      v-for="(item, index) in items"
      :id="`${id}-${item.view}`"
      :key="item.view"
      type="button"
      role="tab"
      :aria-label="item.label"
      :aria-controls="`knowledge-${item.view}-panel`"
      :aria-selected="item.view === activeView"
      :class="{ 'is-active': item.view === activeView }"
      :tabindex="
        item.view === activeView || (!items.some((tab) => tab.view === activeView) && index === 0)
          ? 0
          : -1
      "
      @click="select(item.view)"
    >
      <component :is="item.icon" aria-hidden="true" /><span>{{ item.label }}</span>
    </button>
  </nav>
</template>
<style scoped>
.knowledge-section-nav {
  display: flex;
  flex: 0 0 auto;
  align-items: stretch;
  min-width: 0;
  gap: 26px;
  height: 46px;
  border-bottom: 1px solid var(--gray-150);
}
.knowledge-section-nav button {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 2px;
  border: 0;
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: 14px;
  white-space: nowrap;
  cursor: pointer;
  transition: color 0.15s ease;
}
.knowledge-section-nav button::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  border-radius: 2px;
  background: transparent;
  content: '';
}
.knowledge-section-nav button:hover,
.knowledge-section-nav button.is-active {
  color: var(--main-color);
}
.knowledge-section-nav button.is-active {
  font-weight: 600;
}
.knowledge-section-nav button.is-active::after {
  background: var(--main-color);
}
.knowledge-section-nav button:focus-visible {
  outline: 2px solid var(--main-300);
  outline-offset: -3px;
  border-radius: 4px;
}
</style>
