<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

const props = defineProps<{ details?: unknown }>()
interface Reference {
  kind: string
  id: string | null
  name: string
  status: string
  path: string | null
}
const references = computed<Reference[]>(() => {
  const details = props.details
  if (!details || typeof details !== 'object' || !('references' in details)) return []
  if (!Array.isArray(details.references)) return []
  return details.references.flatMap((value: unknown) => {
    if (!value || typeof value !== 'object') return []
    const item = value as Record<string, unknown>
    if (
      typeof item.kind !== 'string' ||
      (item.id !== null && typeof item.id !== 'string') ||
      typeof item.name !== 'string' ||
      typeof item.status !== 'string' ||
      (item.path !== null && typeof item.path !== 'string')
    )
      return []
    const path =
      typeof item.path === 'string' &&
      item.path.startsWith('/') &&
      !item.path.startsWith('//') &&
      !item.path.includes('\\') &&
      !Array.from(item.path).some((character) => {
        const code = character.charCodeAt(0)
        return code < 32 || code === 127
      }) &&
      item.id !== null
        ? item.path
        : null
    return [{ kind: item.kind, id: item.id, name: item.name, status: item.status, path }]
  })
})
const statusLabel = (status: string): string =>
  ({
    enabled: '已启用',
    disabled: '已停用',
    published: '已发布',
    draft: '草稿',
    active: '使用中',
    referenced: '使用中',
    pending: '等待中',
    running: '运行中',
    cancel_requested: '正在停止',
    retiring: '即将撤销',
    waiting_models: '等待模型',
    waiting_model: '等待模型',
    draining: '正在收尾',
    admitted: '已接纳',
    deleted: '已删除'
  })[status] ?? status
</script>

<template>
  <section v-if="references.length" class="resource-references" aria-label="使用中的资源">
    <h3>使用中的资源</h3>
    <ul>
      <li
        v-for="(item, index) in references"
        :key="`${item.kind}:${item.id ?? 'private'}:${item.status}:${index}`"
      >
        <RouterLink v-if="item.path" :to="item.path">{{ item.name }}</RouterLink>
        <span v-else>{{ item.name }}</span>
        <span class="reference-status">{{ statusLabel(item.status) }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.resource-references {
  margin: 16px 0;
  padding: 14px 16px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--bg-elevated);
}
h3 {
  margin: 0 0 10px;
  color: var(--color-text);
  font-size: 14px;
  font-weight: 500;
}
ul {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
li {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.reference-status {
  flex-shrink: 0;
  color: var(--color-text-secondary);
}
</style>
