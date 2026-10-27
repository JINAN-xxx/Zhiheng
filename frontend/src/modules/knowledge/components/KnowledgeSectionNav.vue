<!-- Adapted from the original knowledge workspace, MIT licensed. -->
<template>
  <nav class="knowledge-section-nav" aria-label="知识库功能导航">
    <a-button
      v-for="item in items"
      :key="item.view"
      :aria-label="item.label"
      :class="{ 'is-active': item.view === activeView }"
      :disabled="item.view === 'graph' && !graphAvailable"
      :aria-current="item.view === activeView ? 'page' : undefined"
      @click="emit('navigate', item.view)"
    >
      <template #icon><component :is="item.icon" /></template>
      {{ item.label }}
    </a-button>
  </nav>
</template>

<script setup lang="ts">
import { ApartmentOutlined, FileTextOutlined, SearchOutlined } from '@ant-design/icons-vue'
import { Button as AButton } from 'ant-design-vue'
defineProps<{ activeView: string; graphAvailable?: boolean }>()
const emit = defineEmits<{ navigate: [view: string] }>()
const items = [
  { view: 'documents', label: '文档管理', icon: FileTextOutlined },
  { view: 'query', label: '检索测试', icon: SearchOutlined },
  { view: 'graph', label: '知识图谱', icon: ApartmentOutlined }
]
</script>
<style scoped lang="scss">
.knowledge-section-nav {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.knowledge-section-nav :deep(.ant-btn) {
  border-radius: 6px;
}

.knowledge-section-nav :deep(.ant-btn.is-active) {
  border-color: var(--main-300);
  color: var(--main-700);
  background: var(--main-50);
  font-weight: 600;
}

@media (max-width: 560px) {
  .knowledge-section-nav {
    flex-wrap: nowrap;
    overflow-x: auto;
    padding-bottom: 2px;
  }

  .knowledge-section-nav :deep(.ant-btn) {
    flex: 0 0 auto;
  }
}
</style>
